#!/usr/bin/env python3
"""Create editable Origin Nyquist plots from area-normalized EIS CSV files."""

from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path
from typing import Any


op: Any = None


COLORS = [
    "#2166AC", "#67A9CF", "#B7D4E8", "#F4A582",
    "#EF8A62", "#D6604D", "#C51B3A",
]
LAYER_WIDTH_CM = 14.0
LAYER_HEIGHT_CM = 10.0
LEGEND_RIGHT_GAP_FRACTION = 0.01
LEGEND_TOP_GAP_FRACTION = 0.032
LEGEND_RENDERING_PAD_FRACTION = 0.114
LEGEND_LEFT_GAP_FRACTION = 0.01
LEGEND_BOTTOM_GAP_FRACTION = 0.01
LEGEND_OVERFLOW_NUDGE_FRACTION = 0.001
SAMPLE_LEFT_FRACTION = 0.06
SAMPLE_TOP_GAP_FRACTION = 0.08
LABEL_COLLISION_GAP_FRACTION = 0.02
MIN_LABEL_FONT_SIZE = 12


def get_originpro():
    """Load the official external Origin API only when plotting is requested."""

    global op
    if op is None:
        try:
            import originpro as origin_api
        except ImportError as exc:
            raise RuntimeError(
                "The official originpro package is required for Origin automation."
            ) from exc
        op = origin_api
    return op


def close_origin() -> None:
    """Close the external Origin instance if one was created."""

    if op is not None:
        try:
            op.exit()
        except Exception:
            pass


def series_info(path: Path) -> tuple[tuple[int, int | str], str, str]:
    """Return a stable sort key, legend label, and Origin sheet name."""
    match = re.search(r"-(\d+)O2", path.name, re.IGNORECASE)
    if match:
        value = int(match.group(1))
        return (0, value), f"{value}% O\\-(2)", f"{value}O2"
    label = re.sub(r"_eis_\d+_area_normalized$", "", path.stem, flags=re.IGNORECASE)
    return (1, label.casefold()), label, re.sub(r"[^A-Za-z0-9_]", "_", label)[:24] or "EIS"


def read_csv(path: Path) -> dict[str, list[float]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = [
        "Frequency_Hz", "H_Real_Ohm", "H_Imaginary_Ohm", "H_Modulus_Ohm",
        "H_Argument_rad", "H_Phase_deg", "Time_s", "Z_real_area_Ohm_cm2",
        "minus_Z_imag_area_Ohm_cm2",
    ]
    if not rows or any(name not in rows[0] for name in required):
        raise ValueError(f"missing required EIS columns in {path}")
    data = {name: [float(row[name]) for row in rows] for name in required}
    if not all(math.isfinite(value) for values in data.values() for value in values):
        raise ValueError(f"non-finite value in {path}")
    return data


def shift_to_left_y0_intercept(
    x_values: list[float], y_values: list[float]
) -> tuple[list[float], list[float]]:
    """Shift the leftmost linearly interpolated Y=0 crossing to (0, 0)."""
    candidates: list[tuple[float, int]] = []
    for index in range(len(y_values) - 1):
        y1, y2 = y_values[index], y_values[index + 1]
        x1, x2 = x_values[index], x_values[index + 1]
        if y1 == 0:
            candidates.append((x1, index))
        if (y1 < 0 <= y2) or (y2 < 0 <= y1):
            fraction = -y1 / (y2 - y1)
            candidates.append((x1 + fraction * (x2 - x1), index))
    if y_values[-1] == 0:
        candidates.append((x_values[-1], len(y_values) - 1))
    if not candidates:
        raise ValueError("curve has no Y=0 crossing")

    crossing_x, segment_index = min(candidates, key=lambda item: item[0])
    start = segment_index + 1
    shifted_x = [0.0] + [value - crossing_x for value in x_values[start:]]
    shifted_y = [0.0] + y_values[start:]
    while len(shifted_y) > 1 and shifted_y[1] < -1e-12:
        shifted_x.pop(1)
        shifted_y.pop(1)
    return shifted_x, shifted_y


def nice_step(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 0.1
    power = 10 ** math.floor(math.log10(value))
    fraction = value / power
    factor = 1 if fraction <= 1 else 2 if fraction <= 2 else 5 if fraction <= 5 else 10
    return factor * power


def common_axis(series: list[tuple[list[float], list[float]]], intercept_zero: bool) -> tuple[float, float, float]:
    values = [value for x_values, y_values in series for value in (*x_values, *y_values)]
    if not values:
        raise ValueError("no values available for axis scaling")
    data_low = min(values)
    data_high = max(values)
    low_seed = 0.0 if intercept_zero else min(0.0, data_low)
    span = max(data_high - low_seed, 1e-12)
    step = nice_step(span / 5.0)
    low = 0.0 if intercept_zero else math.floor(low_seed / step) * step
    high = math.ceil(max(0.0, data_high) / step) * step
    if high <= low:
        high = low + 5 * step
    return low, high, step


def set_text_style(label, font_index: int, size: float) -> None:
    if label is None:
        return
    label.set_int("font", font_index)
    label.set_float("fsize", size)
    label.set_int("bold", 0)
    label.color = "#000000"


def text_size_in_data_units(label, axis_span: float) -> tuple[float, float]:
    width_inches = max(float(label.obj.GetWidth()), 0.0) / 1000.0
    height_inches = max(float(label.obj.GetHeight()), 0.0) / 1000.0
    width_data = width_inches / (LAYER_WIDTH_CM / 2.54) * axis_span
    height_data = height_inches / (LAYER_HEIGHT_CM / 2.54) * axis_span
    return width_data, height_data


def text_bounds(label, axis_span: float) -> tuple[float, float, float, float]:
    width, height = text_size_in_data_units(label, axis_span)
    left = label.get_float("x1")
    top = label.get_float("y1")
    return left, left + width, top - height, top


def legend_bounds(legend, axis_span: float) -> tuple[float, float, float, float]:
    left, right, bottom, top = text_bounds(legend, axis_span)
    return left, right + LEGEND_RENDERING_PAD_FRACTION * axis_span, bottom, top


def bounds_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    gap: float,
) -> bool:
    first_left, first_right, first_bottom, first_top = first
    second_left, second_right, second_bottom, second_top = second
    return not (
        first_right + gap <= second_left
        or second_right + gap <= first_left
        or first_top + gap <= second_bottom
        or second_top + gap <= first_bottom
    )


def update_origin_layout() -> None:
    op.lt_exec("doc -uw;sec -p 0.2;")


def align_legend_to_right_axis(legend, axis_low: float, axis_high: float, axis_span: float) -> None:
    left_limit = axis_low + LEGEND_LEFT_GAP_FRACTION * axis_span
    bottom_limit = axis_low + LEGEND_BOTTOM_GAP_FRACTION * axis_span
    target_right = axis_high - LEGEND_RIGHT_GAP_FRACTION * axis_span
    target_top = axis_high - LEGEND_TOP_GAP_FRACTION * axis_span

    for _attempt in range(12):
        update_origin_layout()
        legend_width, _legend_height = text_size_in_data_units(legend, axis_span)
        legend_width += LEGEND_RENDERING_PAD_FRACTION * axis_span
        legend.set_float("x1", max(left_limit, target_right - legend_width))
        legend.set_float("y1", target_top)
        update_origin_layout()

        left, right, bottom, top = legend_bounds(legend, axis_span)
        right_overflow = right - target_right
        bottom_overflow = bottom_limit - bottom
        if right_overflow <= 1e-12 and bottom_overflow <= 1e-12 and top <= axis_high:
            return

        if right_overflow > 0 and left > left_limit + 1e-12:
            legend.set_float(
                "x1",
                max(left_limit, legend.get_float("x1") - right_overflow - LEGEND_OVERFLOW_NUDGE_FRACTION * axis_span),
            )
            update_origin_layout()
            continue

        font_size = legend.get_float("fsize")
        if font_size <= MIN_LABEL_FONT_SIZE:
            break
        legend.set_float("fsize", font_size - 1)

    left, right, bottom, top = legend_bounds(legend, axis_span)
    if left < axis_low - 1e-12 or right > axis_high + 1e-12 or bottom < axis_low - 1e-12 or top > axis_high + 1e-12:
        raise RuntimeError("legend cannot fit inside the graph layer without touching or crossing an axis")


def avoid_sample_legend_overlap(sample, legend, axis_low: float, axis_span: float) -> None:
    gap = LABEL_COLLISION_GAP_FRACTION * axis_span
    for _attempt in range(12):
        update_origin_layout()
        sample_box = text_bounds(sample, axis_span)
        legend_box = legend_bounds(legend, axis_span)
        if not bounds_overlap(sample_box, legend_box, gap):
            return

        _sample_left, _sample_right, sample_bottom, sample_top = sample_box
        _legend_left, _legend_right, legend_bottom, _legend_top = legend_box
        sample_height = sample_top - sample_bottom
        new_top = legend_bottom - gap
        min_top = axis_low + sample_height + gap

        if new_top >= min_top:
            sample.set_float("y1", new_top)
        else:
            sample.set_float("y1", min_top)
            font_size = sample.get_float("fsize")
            if font_size <= MIN_LABEL_FONT_SIZE:
                break
            sample.set_float("fsize", font_size - 1)
        update_origin_layout()

    if bounds_overlap(text_bounds(sample, axis_span), legend_bounds(legend, axis_span), gap):
        raise RuntimeError("sample label overlaps the legend after automatic layout adjustment")


def safe_stem(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", value).strip(" ._")
    return cleaned or "EIS"


def build_plot(
    input_dir: Path,
    output_dir: Path,
    sample_label: str,
    intercept_zero: bool = False,
    *,
    close_origin_on_exit: bool = True,
) -> dict[str, str]:
    get_originpro()
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    csv_files = list(input_dir.glob("*_area_normalized.csv"))
    csv_files.sort(key=lambda path: series_info(path)[0])
    if not csv_files:
        raise ValueError(f"no area-normalized CSV files found in {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    prepared = []
    axis_series = []
    for csv_path in csv_files:
        _key, legend_text, sheet_name = series_info(csv_path)
        data = read_csv(csv_path)
        x_abs = data["Z_real_area_Ohm_cm2"]
        y_abs = data["minus_Z_imag_area_Ohm_cm2"]
        x_plot, y_plot = shift_to_left_y0_intercept(x_abs, y_abs) if intercept_zero else (x_abs, y_abs)
        prepared.append((csv_path, legend_text, sheet_name, data, x_abs, y_abs, x_plot, y_plot))
        axis_series.append((x_plot, y_plot))
    axis_low, axis_high, axis_step = common_axis(axis_series, intercept_zero)
    axis_span = axis_high - axis_low

    op.set_show(False)
    op.new()
    try:
        font_index = int(op.lt_float("font(Arial)"))
        book = op.new_book("w", "EIS Data")
        if book is None:
            raise RuntimeError("Origin failed to create workbook")

        sheets = []
        for index, item in enumerate(prepared):
            csv_path, legend_text, sheet_name, data, x_abs, y_abs, x_plot, y_plot = item
            sheet = book[0] if index == 0 else book.add_sheet()
            sheet.name = sheet_name
            columns = [
                (data["Frequency_Hz"], "Frequency", "Hz", "N"),
                (data["H_Real_Ohm"], "H_Real", "Ω", "N"),
                (data["H_Imaginary_Ohm"], "H_Imaginary (NOVA -Z'')", "Ω", "N"),
                (data["H_Modulus_Ohm"], "H_Modulus", "Ω", "N"),
                (data["H_Argument_rad"], "H_Argument", "rad", "N"),
                (data["H_Phase_deg"], "H_Phase", "deg", "N"),
                (data["Time_s"], "Time", "s", "N"),
                (x_abs, "Z'·A", "Ω·cm²", "N" if intercept_zero else "X"),
                (y_abs, "-Z''·A", "Ω·cm²", "N" if intercept_zero else "Y"),
            ]
            if intercept_zero:
                columns.extend([
                    (x_plot, "Z'·A (left Y=0 intercept shifted)", "Ω·cm²", "X"),
                    (y_plot, "-Z''·A (from left Y=0 intercept)", "Ω·cm²", "Y"),
                ])
            sheet.cols = len(columns)
            for col, (values, long_name, units, axis) in enumerate(columns):
                sheet.from_list(col, values, lname=long_name, units=units, axis=axis)
            sheet.set_label(0, csv_path.name, "C")
            sheets.append((sheet, legend_text))

        graph = op.new_graph("EIS Nyquist", template="Origin")
        if graph is None:
            raise RuntimeError("Origin failed to create graph")
        graph.obj.Width = 8.0
        graph.obj.Height = 6.3
        layer = graph[0]
        for index, (sheet, _legend_text) in enumerate(sheets):
            plot = layer.add_plot(
                sheet,
                coly=10 if intercept_zero else 8,
                colx=9 if intercept_zero else 7,
                type="y",
            )
            if plot is None:
                raise RuntimeError(f"failed to plot series {index + 1}")
            plot.color = COLORS[index % len(COLORS)]
            plot.symbol_kind = 2
            plot.symbol_interior = 0
            plot.symbol_size = 7
            plot.set_cmd("-d 0", "-wp 1.2")

        graph.activate()
        axis_script = (
            f"layer.x.from={axis_low:.12g};layer.x.to={axis_high:.12g};layer.x.inc={axis_step:.12g};"
            f"layer.y.from={axis_low:.12g};layer.y.to={axis_high:.12g};layer.y.inc={axis_step:.12g};"
        )
        op.lt_exec(
            "page.updatetoprinter=0;"
            f"layer.unit=3;layer.left=3;layer.top=2.7;layer.width={LAYER_WIDTH_CM};layer.height={LAYER_HEIGHT_CM};"
            + axis_script
            + "layer.x.showGrids=0;layer.y.showGrids=0;"
            "layer.x.showopposite=1;layer.y.showopposite=1;"
            "layer.x.ticks=10;layer.y.ticks=10;"
            "layer.x.mticks=1;layer.y.mticks=1;"
            "layer.x2.ticks=0;layer.y2.ticks=0;"
            "layer.x2.mticks=0;layer.y2.mticks=0;"
            "layer.x.thickness=2;layer.y.thickness=2;"
            "layer.x2.thickness=2;layer.y2.thickness=2;"
            "layer.x.tickthickness=2;layer.y.tickthickness=2;"
            "layer.x.label.pt=20;layer.y.label.pt=20;"
            "layer.x.label.font=font(Arial);layer.y.label.font=font(Arial);"
            "layer.x.label.bold=0;layer.y.label.bold=0;page.ytitle=15;"
        )

        layer.axis("x").title = r"Z′ (Ω cm\+(2))"
        layer.axis("y").title = r"-Z″ (Ω cm\+(2))"
        set_text_style(layer.label("xb"), font_index, 22)
        set_text_style(layer.label("yl"), font_index, 22)

        sample = layer.add_label(
            sample_label,
            axis_low + SAMPLE_LEFT_FRACTION * axis_span,
            axis_high - SAMPLE_TOP_GAP_FRACTION * axis_span,
        )
        set_text_style(sample, font_index, 22)

        layer.lt_exec("legend -r")
        legend = layer.label("Legend") or layer.label("legend")
        if legend is None:
            raise RuntimeError("Origin failed to create legend")
        legend.text = "\r\n".join(
            f"\\l({index + 1}) {legend_text}"
            for index, (_sheet, legend_text) in enumerate(sheets)
        )
        legend.set_int("attach", 0)
        legend.set_int("border", 0)
        legend.set_int("background", 0)
        set_text_style(legend, font_index, 17)
        align_legend_to_right_axis(legend, axis_low, axis_high, axis_span)
        avoid_sample_legend_overlap(sample, legend, axis_low, axis_span)

        graph.activate()
        update_origin_layout()

        suffix = "final" if intercept_zero else "original"
        stem = f"{safe_stem(sample_label)}_EIS_{suffix}"
        opju = output_dir / f"{stem}.opju"
        png = output_dir / f"{stem}.png"
        if not op.save(str(opju)):
            raise RuntimeError("Origin failed to save OPJU")
        exported = graph.save_fig(str(png), type="png", width=1800)
        if not exported or not Path(exported).exists():
            raise RuntimeError("Origin failed to export PNG")
        return {"opju": str(opju), "png": str(png)}
    finally:
        if close_origin_on_exit:
            close_origin()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-label", required=True)
    parser.add_argument("--intercept-zero", action="store_true")
    args = parser.parse_args()
    outputs = build_plot(args.input_dir, args.output_dir, args.sample_label, args.intercept_zero)
    print(f"OPJU: {outputs['opju']}")
    print(f"PNG: {outputs['png']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
