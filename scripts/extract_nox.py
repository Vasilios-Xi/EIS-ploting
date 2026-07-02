#!/usr/bin/env python3
"""Safely extract EIS signals from Metrohm Autolab NOVA .nox files.

The reader implements the relevant MS-NRBF records directly.  It never imports,
instantiates, or executes the .NET types named by the input file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO


class NrbfError(ValueError):
    pass


@dataclass
class Reference:
    object_id: int


@dataclass
class NullRun:
    count: int


@dataclass
class NrbfObject:
    object_id: int
    type_name: str
    members: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassMetadata:
    type_name: str
    member_names: list[str]
    member_types: list[tuple[int, Any]] | None


class NrbfReader:
    RECORD_NAMES = {
        0: "SerializedStreamHeader", 1: "ClassWithId",
        2: "SystemClassWithMembers", 3: "ClassWithMembers",
        4: "SystemClassWithMembersAndTypes", 5: "ClassWithMembersAndTypes",
        6: "BinaryObjectString", 7: "BinaryArray", 8: "MemberPrimitiveTyped",
        9: "MemberReference", 10: "ObjectNull", 11: "MessageEnd",
        12: "BinaryLibrary", 13: "ObjectNullMultiple256",
        14: "ObjectNullMultiple", 15: "ArraySinglePrimitive",
        16: "ArraySingleObject", 17: "ArraySingleString",
        21: "MethodCall", 22: "MethodReturn",
    }

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.objects: dict[int, Any] = {}
        self.metadata: dict[int, ClassMetadata] = {}
        self.libraries: dict[int, str] = {}
        self.root_id: int | None = None

    @property
    def position(self) -> int:
        return self.stream.tell()

    def read_exact(self, count: int) -> bytes:
        data = self.stream.read(count)
        if len(data) != count:
            raise NrbfError(f"unexpected EOF at offset {self.position}")
        return data

    def u8(self) -> int:
        return self.read_exact(1)[0]

    def i16(self) -> int:
        return struct.unpack("<h", self.read_exact(2))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read_exact(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read_exact(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read_exact(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.read_exact(8))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read_exact(8))[0]

    def read_7bit_int(self) -> int:
        value = 0
        shift = 0
        for _ in range(5):
            byte = self.u8()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
        raise NrbfError(f"invalid length-prefixed string at offset {self.position}")

    def string(self) -> str:
        length = self.read_7bit_int()
        if length > 256 * 1024 * 1024:
            raise NrbfError(f"unreasonable string length {length}")
        return self.read_exact(length).decode("utf-8", errors="strict")

    def primitive(self, primitive_type: int) -> Any:
        if primitive_type == 1:  # Boolean
            return self.u8() != 0
        if primitive_type == 2:  # Byte
            return self.u8()
        if primitive_type == 3:  # Char
            return chr(self.u16())
        if primitive_type == 5:  # Decimal (four Int32 fields)
            lo, mid, hi, flags = struct.unpack("<IIII", self.read_exact(16))
            scale = (flags >> 16) & 0x7F
            sign = -1 if flags & 0x80000000 else 1
            return sign * (lo + (mid << 32) + (hi << 64)) / (10 ** scale)
        if primitive_type == 6:
            return struct.unpack("<d", self.read_exact(8))[0]
        if primitive_type == 7:
            return self.i16()
        if primitive_type == 8:
            return self.i32()
        if primitive_type in (9, 12, 13):  # Int64, TimeSpan, DateTime
            return self.i64()
        if primitive_type == 10:
            return struct.unpack("<b", self.read_exact(1))[0]
        if primitive_type == 11:
            return struct.unpack("<f", self.read_exact(4))[0]
        if primitive_type == 14:
            return self.u16()
        if primitive_type == 15:
            return self.u32()
        if primitive_type == 16:
            return self.u64()
        if primitive_type == 17:
            return None
        if primitive_type == 18:
            return self.string()
        raise NrbfError(f"unsupported primitive type {primitive_type} at offset {self.position}")

    def class_info(self) -> tuple[int, str, list[str]]:
        object_id = self.i32()
        type_name = self.string()
        member_count = self.i32()
        if member_count < 0 or member_count > 1_000_000:
            raise NrbfError(f"invalid member count {member_count}")
        names = [self.string() for _ in range(member_count)]
        return object_id, type_name, names

    def additional_type_info(self, binary_type: int) -> Any:
        if binary_type == 0:  # Primitive
            return self.u8()
        if binary_type == 3:  # SystemClass
            return self.string()
        if binary_type == 4:  # Class
            return (self.string(), self.i32())
        if binary_type == 7:  # PrimitiveArray
            return self.u8()
        if binary_type in (1, 2, 5, 6):
            return None
        raise NrbfError(f"unsupported binary type {binary_type}")

    def member_type_info(self, count: int) -> list[tuple[int, Any]]:
        binary_types = [self.u8() for _ in range(count)]
        return [(bt, self.additional_type_info(bt)) for bt in binary_types]

    def read_member_value(self, type_info: tuple[int, Any]) -> Any:
        binary_type, additional = type_info
        if binary_type == 0:
            return self.primitive(additional)
        return self.read_object_record()

    def read_array_values(self, count: int, type_info: tuple[int, Any]) -> list[Any]:
        if count < 0 or count > 100_000_000:
            raise NrbfError(f"unreasonable array length {count}")
        binary_type, additional = type_info
        if binary_type == 0:
            return [self.primitive(additional) for _ in range(count)]
        values: list[Any] = []
        while len(values) < count:
            value = self.read_object_record()
            if isinstance(value, NullRun):
                values.extend([None] * value.count)
            else:
                values.append(value)
        if len(values) != count:
            raise NrbfError(f"array null run exceeded declared length {count}")
        return values

    def read_class(self, record_type: int) -> NrbfObject:
        if record_type == 1:  # ClassWithId
            object_id = self.i32()
            metadata_id = self.i32()
            if metadata_id not in self.metadata:
                raise NrbfError(f"unknown metadata id {metadata_id}")
            meta = self.metadata[metadata_id]
        else:
            object_id, type_name, names = self.class_info()
            member_types = None
            if record_type in (4, 5):
                member_types = self.member_type_info(len(names))
            if record_type in (3, 5):
                self.i32()  # library id
            meta = ClassMetadata(type_name, names, member_types)
            self.metadata[object_id] = meta

        obj = NrbfObject(object_id, meta.type_name)
        self.objects[object_id] = obj  # register before members for cycles
        for index, name in enumerate(meta.member_names):
            if meta.member_types is None:
                obj.members[name] = self.read_object_record()
            else:
                obj.members[name] = self.read_member_value(meta.member_types[index])
        return obj

    def read_object_record(self) -> Any:
        while True:
            offset = self.position
            record_type = self.u8()
            if record_type == 12:  # BinaryLibrary may precede an object record
                library_id = self.i32()
                self.libraries[library_id] = self.string()
                continue
            break

        if record_type in (1, 2, 3, 4, 5):
            return self.read_class(record_type)
        if record_type == 6:
            object_id = self.i32()
            value = self.string()
            self.objects[object_id] = value
            return value
        if record_type == 7:
            object_id = self.i32()
            array_type = self.u8()
            rank = self.i32()
            if rank <= 0 or rank > 64:
                raise NrbfError(f"invalid array rank {rank}")
            lengths = [self.i32() for _ in range(rank)]
            if array_type in (3, 4, 5):
                _lower_bounds = [self.i32() for _ in range(rank)]
            type_info = (self.u8(), None)
            type_info = (type_info[0], self.additional_type_info(type_info[0]))
            count = math.prod(lengths)
            values = self.read_array_values(count, type_info)
            result = {"shape": lengths, "values": values}
            self.objects[object_id] = result
            return result
        if record_type == 8:
            return self.primitive(self.u8())
        if record_type == 9:
            return Reference(self.i32())
        if record_type == 10:
            return None
        if record_type == 11:
            return _MESSAGE_END
        if record_type == 13:
            return NullRun(self.u8())
        if record_type == 14:
            return NullRun(self.i32())
        if record_type == 15:
            object_id = self.i32()
            count = self.i32()
            primitive_type = self.u8()
            values = [self.primitive(primitive_type) for _ in range(count)]
            self.objects[object_id] = values
            return values
        if record_type in (16, 17):
            object_id = self.i32()
            count = self.i32()
            binary_type = 2 if record_type == 16 else 1
            values = self.read_array_values(count, (binary_type, None))
            self.objects[object_id] = values
            return values
        name = self.RECORD_NAMES.get(record_type, "unknown")
        raise NrbfError(f"unsupported record {record_type} ({name}) at offset {offset}")

    def read_stream(self) -> Any:
        start = self.position
        if self.u8() != 0:
            raise NrbfError(f"missing NRBF stream header at offset {start}")
        self.root_id = self.i32()
        _header_id = self.i32()
        major = self.i32()
        minor = self.i32()
        if (major, minor) != (1, 0):
            raise NrbfError(f"unsupported NRBF version {major}.{minor}")

        root: Any = None
        while True:
            value = self.read_object_record()
            if value is _MESSAGE_END:
                break
            if root is None and self.root_id in self.objects:
                root = self.objects[self.root_id]
        if root is None:
            root = self.objects.get(self.root_id)
        return root


_MESSAGE_END = object()


def dereference(value: Any, objects: dict[int, Any]) -> Any:
    if isinstance(value, Reference):
        return objects.get(value.object_id, value)
    return value


def unwrap_sequence(value: Any, objects: dict[int, Any]) -> list[Any] | None:
    """Return values from an NRBF array or List<T> without constructing .NET types."""
    value = dereference(value, objects)
    if isinstance(value, list):
        return [dereference(item, objects) for item in value]
    if isinstance(value, dict) and "values" in value:
        return [dereference(item, objects) for item in value["values"]]
    if isinstance(value, NrbfObject) and value.type_name.startswith("System.Collections.Generic.List`1"):
        size = value.members.get("_size")
        items = unwrap_sequence(value.members.get("_items"), objects)
        if isinstance(size, int) and items is not None:
            return items[:size]
    return None


def describe_value(value: Any, objects: dict[int, Any]) -> str:
    value = dereference(value, objects)
    sequence = unwrap_sequence(value, objects)
    if sequence is not None:
        first = sequence[0] if sequence else None
        last = sequence[-1] if sequence else None
        return f"sequence[{len(sequence)}] first={first!r} last={last!r}"
    if isinstance(value, NrbfObject):
        return f"object:{value.type_name}#{value.object_id}"
    if isinstance(value, list):
        first = value[0] if value else None
        last = value[-1] if value else None
        return f"list[{len(value)}] first={first!r} last={last!r}"
    if isinstance(value, dict) and "shape" in value:
        return f"array{value['shape']}"
    if isinstance(value, str):
        text = value.replace("\r", " ").replace("\n", " ")
        return repr(text[:100] + ("..." if len(text) > 100 else ""))
    return repr(value)


def collect_signal_groups(reader: NrbfReader) -> list[dict[str, Any]]:
    target_names = {
        "Frequency", "H_Real", "H_Imaginary", "H_Modulus",
        "H_Argument", "H_Phase", "Time",
    }
    groups: dict[int, dict[str, Any]] = {}
    for value in reader.objects.values():
        if not isinstance(value, NrbfObject):
            continue
        name = dereference(
            value.members.get("_name", value.members.get("CommandParameter+_name")),
            reader.objects,
        )
        if name not in target_names:
            continue
        parent = dereference(
            value.members.get("_parent", value.members.get("CommandParameter+_parent")),
            reader.objects,
        )
        if not isinstance(parent, NrbfObject):
            continue
        parameter = dereference(
            value.members.get("_parameter", value.members.get("CommandParameter+_parameter")),
            reader.objects,
        )
        if not isinstance(parameter, NrbfObject):
            continue
        sequence = unwrap_sequence(parameter.members.get("_value"), reader.objects)
        if sequence is None:
            continue
        group = groups.setdefault(parent.object_id, {
            "parent_id": parent.object_id,
            "parent_type": parent.type_name,
            "signals": {},
        })
        group["signals"][name] = sequence
    return list(groups.values())


def _all_finite(values: list[Any]) -> bool:
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)


def _match_argument(signals: dict[str, list[Any]], fia_groups: list[dict[str, Any]]) -> list[float]:
    exact: dict[tuple[float, float, float], float] = {}
    rows: list[tuple[float, float, float, float]] = []
    for group in fia_groups:
        row = group["signals"]
        required = ("Frequency", "H_Real", "H_Imaginary", "H_Argument")
        if not all(name in row and len(row[name]) == 1 for name in required):
            continue
        frequency, real, imaginary, argument = (float(row[name][0]) for name in required)
        exact[(frequency, real, imaginary)] = argument
        rows.append((frequency, real, imaginary, argument))

    result: list[float] = []
    for frequency, real, imaginary in zip(
        signals["Frequency"], signals["H_Real"], signals["H_Imaginary"]
    ):
        key = (float(frequency), float(real), float(imaginary))
        argument = exact.get(key)
        if argument is None:
            matches = [row for row in rows if math.isclose(row[0], key[0], rel_tol=1e-12, abs_tol=1e-12)
                       and math.isclose(row[1], key[1], rel_tol=1e-12, abs_tol=1e-12)
                       and math.isclose(row[2], key[2], rel_tol=1e-12, abs_tol=1e-12)]
            if len(matches) != 1:
                raise NrbfError(f"could not uniquely match H_Argument at frequency {frequency}")
            argument = matches[0][3]
        result.append(float(argument))
    return result


def extract_datasets(path: Path) -> tuple[list[dict[str, list[float]]], dict[str, Any]]:
    candidates: list[dict[str, list[float]]] = []
    stream_ranges: list[dict[str, int]] = []
    internal_strings: set[str] = set()
    with path.open("rb") as stream:
        stream_index = 0
        while stream.tell() < path.stat().st_size:
            start = stream.tell()
            reader = NrbfReader(stream)
            reader.read_stream()
            stream_ranges.append({"index": stream_index, "start": start, "end": stream.tell()})
            internal_strings.update(value for value in reader.objects.values() if isinstance(value, str))
            groups = collect_signal_groups(reader)
            fia_groups = [group for group in groups if group["parent_type"] == "EcoChemie.Autolab.FIAMeasurement"]
            for group in groups:
                if group["parent_type"] != "EcoChemie.Shared.SignalBuilderCommand":
                    continue
                signals = group["signals"]
                required = ("Frequency", "H_Real", "H_Imaginary", "H_Modulus", "H_Phase", "Time")
                if not all(name in signals for name in required):
                    continue
                lengths = {len(signals[name]) for name in required}
                if len(lengths) != 1 or next(iter(lengths)) == 0:
                    continue
                if not all(_all_finite(signals[name]) for name in required):
                    raise NrbfError(f"non-finite EIS value in SignalBuilderCommand {group['parent_id']}")
                dataset = {name: [float(v) for v in signals[name]] for name in required}
                dataset["H_Argument"] = _match_argument(dataset, fia_groups)
                candidates.append(dataset)
            stream_index += 1

    unique: list[dict[str, list[float]]] = []
    seen: set[tuple[tuple[float, ...], ...]] = set()
    signal_order = ("Frequency", "H_Real", "H_Imaginary", "H_Modulus", "H_Argument", "H_Phase", "Time")
    for dataset in candidates:
        signature = tuple(tuple(dataset[name]) for name in signal_order)
        if signature not in seen:
            seen.add(signature)
            unique.append(dataset)
    if not unique:
        raise NrbfError("no complete EIS SignalBuilderCommand dataset found")

    labels = sorted({value for value in internal_strings if path.stem in value and len(value) < 300})
    source_meta = {
        "source_file": path.name,
        "source_path": str(path.resolve()),
        "source_size_bytes": path.stat().st_size,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "nrbf_streams": stream_ranges,
        "internal_label_candidates": labels,
    }
    return unique, source_meta


def normalize_dataset(dataset: dict[str, list[float]], area_cm2: float) -> tuple[list[dict[str, float]], dict[str, Any]]:
    count = len(dataset["Frequency"])
    x_abs = [value * area_cm2 for value in dataset["H_Real"]]
    y_abs = [value * area_cm2 for value in dataset["H_Imaginary"]]

    rows: list[dict[str, float]] = []
    for index in range(count):
        rows.append({
            "Frequency_Hz": dataset["Frequency"][index],
            "H_Real_Ohm": dataset["H_Real"][index],
            "H_Imaginary_Ohm": dataset["H_Imaginary"][index],
            "H_Modulus_Ohm": dataset["H_Modulus"][index],
            "H_Argument_rad": dataset["H_Argument"][index],
            "H_Phase_deg": dataset["H_Phase"][index],
            "Time_s": dataset["Time"][index],
            "Z_real_area_Ohm_cm2": x_abs[index],
            "minus_Z_imag_area_Ohm_cm2": y_abs[index],
        })

    modulus_errors = [abs(modulus - math.hypot(real, imaginary)) for modulus, real, imaginary in zip(
        dataset["H_Modulus"], dataset["H_Real"], dataset["H_Imaginary"]
    )]
    argument_errors = [abs(argument - math.atan2(imaginary, real)) for argument, real, imaginary in zip(
        dataset["H_Argument"], dataset["H_Real"], dataset["H_Imaginary"]
    )]
    phase_errors = [abs(phase - math.degrees(math.atan2(imaginary, real))) for phase, real, imaginary in zip(
        dataset["H_Phase"], dataset["H_Real"], dataset["H_Imaginary"]
    )]
    qa = {
        "point_count": count,
        "frequency_max_Hz": max(dataset["Frequency"]),
        "frequency_min_Hz": min(dataset["Frequency"]),
        "frequency_descending": all(a >= b for a, b in zip(dataset["Frequency"], dataset["Frequency"][1:])),
        "max_modulus_absolute_error_Ohm": max(modulus_errors),
        "max_argument_absolute_error_rad": max(argument_errors),
        "max_phase_absolute_error_deg": max(phase_errors),
        "area_normalization_max_error": max(
            [abs(value - raw * area_cm2) for value, raw in zip(x_abs, dataset["H_Real"])]
            + [abs(value - raw * area_cm2) for value, raw in zip(y_abs, dataset["H_Imaginary"])]
        ),
        "all_values_finite": all(math.isfinite(value) for row in rows for value in row.values()),
    }
    return rows, qa


def write_dataset(path: Path, output_dir: Path, area_cm2: float, dataset_index: int,
                  dataset: dict[str, list[float]], source_meta: dict[str, Any]) -> dict[str, Any]:
    rows, qa = normalize_dataset(dataset, area_cm2)
    stem = f"{path.stem}_eis_{dataset_index:02d}_area_normalized"
    csv_path = output_dir / f"{stem}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {"source": path.name, "dataset": dataset_index, "csv": csv_path.name, **qa}


def process_inputs(input_path: Path, output_dir: Path, area_cm2: float) -> list[dict[str, Any]]:
    files = sorted(input_path.glob("*.nox")) if input_path.is_dir() else [input_path]
    if not files:
        raise NrbfError(f"no .nox files found in {input_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []
    for path in files:
        datasets, source_meta = extract_datasets(path)
        for index, dataset in enumerate(datasets, 1):
            summary.append(write_dataset(path, output_dir, area_cm2, index, dataset, source_meta))

    return summary


def inspect_nox(path: Path) -> None:
    with path.open("rb") as stream:
        stream_index = 0
        while stream.tell() < path.stat().st_size:
            start = stream.tell()
            reader = NrbfReader(stream)
            root = reader.read_stream()
            print(f"stream={stream_index} start={start} end={stream.tell()} root={describe_value(root, reader.objects)} objects={len(reader.objects)}")
            groups = collect_signal_groups(reader)
            for group in groups:
                signals = group["signals"]
                if len(signals) < 5 or max((len(v) for v in signals.values()), default=0) == 0:
                    continue
                lengths = ", ".join(f"{name}={len(values)}" for name, values in sorted(signals.items()))
                print(f"  group parent={group['parent_id']} type={group['parent_type']} {lengths}")
            stream_index += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--area-cm2", type=float)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.inspect:
            inspect_nox(args.input)
        else:
            if args.area_cm2 is None or not math.isfinite(args.area_cm2) or args.area_cm2 <= 0:
                raise NrbfError("--area-cm2 must be a finite positive number")
            if args.output_dir is None:
                raise NrbfError("--output-dir is required")
            summary = process_inputs(args.input, args.output_dir, args.area_cm2)
            for item in summary:
                print(f"{item['source']}: {item['point_count']} points -> {item['csv']}")
    except (OSError, NrbfError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
