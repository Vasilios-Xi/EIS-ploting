"""Reusable orchestration and verification for the EIS plotting workflow."""

from __future__ import annotations

import csv
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from scripts.extract_nox import process_inputs
from scripts.plot_eis_origin import build_plot, close_origin


OUTPUT_FOLDERS = ("NOX处理结果", "原始EIS图", "最终EIS图")
CSV_REQUIRED_COLUMNS = (
    "Frequency_Hz",
    "H_Real_Ohm",
    "H_Imaginary_Ohm",
    "H_Modulus_Ohm",
    "H_Argument_rad",
    "H_Phase_deg",
    "Time_s",
    "Z_real_area_Ohm_cm2",
    "minus_Z_imag_area_Ohm_cm2",
)


def list_nox_files(input_path: Path | str) -> list[Path]:
    path = Path(input_path).resolve()
    if path.is_file():
        if path.suffix.lower() != ".nox":
            raise ValueError("输入文件必须是 .nox。")
        return [path]
    if path.is_dir():
        files = sorted(
            (
                item
                for item in path.iterdir()
                if item.is_file() and item.suffix.lower() == ".nox"
            ),
            key=lambda item: item.name.casefold(),
        )
        if not files:
            raise ValueError(f"文件夹中没有 .nox 文件：{path}")
        return files
    raise FileNotFoundError(f"输入路径不存在：{path}")


def validate_area(area_cm2: float) -> float:
    value = float(area_cm2)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("有效电极面积必须是大于 0 的有限数值。")
    return value


def infer_sample_label(input_path: Path | str) -> str:
    files = list_nox_files(input_path)
    match = re.match(r"([^-_]+)", files[0].stem)
    return match.group(1).strip() if match and match.group(1).strip() else "EIS"


def _verify_csv(path: Path, area_cm2: float) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"归一化 CSV 为空：{path}")
    if any(column not in rows[0] for column in CSV_REQUIRED_COLUMNS):
        raise ValueError(f"归一化 CSV 缺少必要列：{path}")

    maximum_area_error = 0.0
    for row_number, row in enumerate(rows, start=2):
        try:
            values = {
                column: float(row[column]) for column in CSV_REQUIRED_COLUMNS
            }
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CSV 第 {row_number} 行包含非数值：{path}") from exc
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError(f"CSV 第 {row_number} 行包含非有限数值：{path}")
        maximum_area_error = max(
            maximum_area_error,
            abs(
                values["Z_real_area_Ohm_cm2"]
                - values["H_Real_Ohm"] * area_cm2
            ),
            abs(
                values["minus_Z_imag_area_Ohm_cm2"]
                - values["H_Imaginary_Ohm"] * area_cm2
            ),
        )
    tolerance = max(1e-12, abs(area_cm2) * 1e-10)
    if maximum_area_error > tolerance:
        raise ValueError(
            f"面积归一化校验失败：最大误差 {maximum_area_error:.6g}"
        )
    return {
        "file": path.name,
        "rows": len(rows),
        "area_normalization_max_error": maximum_area_error,
    }


def _verify_origin_output(opju: Path, png: Path) -> dict[str, Any]:
    if not opju.is_file() or opju.stat().st_size < 1024:
        raise ValueError(f"OPJU 缺失或文件过小：{opju}")
    if not png.is_file() or png.stat().st_size < 1024:
        raise ValueError(f"PNG 缺失或文件过小：{png}")
    with Image.open(png) as image:
        image.verify()
    with Image.open(png) as image:
        size = image.size
        mode = image.mode
    if size[0] != 1800:
        raise ValueError(f"PNG 宽度应为 1800 px，实际为 {size[0]} px：{png}")
    return {
        "opju": opju.name,
        "opju_bytes": opju.stat().st_size,
        "png": png.name,
        "png_bytes": png.stat().st_size,
        "png_size": list(size),
        "png_mode": mode,
    }


def _known_target(output_directory: Path, folder_name: str) -> Path:
    root = output_directory.resolve()
    target = (root / folder_name).resolve()
    if target.parent != root or target.name != folder_name:
        raise RuntimeError(f"拒绝操作输出目录之外的路径：{target}")
    return target


def existing_output_folders(output_directory: Path | str) -> list[Path]:
    root = Path(output_directory).resolve()
    return [
        target
        for name in OUTPUT_FOLDERS
        if (target := _known_target(root, name)).exists()
    ]


def _retry_sharing_violation(
    operation: Callable[[], Any],
    *,
    timeout_seconds: float = 15.0,
    delay_seconds: float = 0.2,
) -> Any:
    """Retry a filesystem operation while Origin releases a Windows file lock."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return operation()
        except OSError as exc:
            sharing_violation = isinstance(exc, PermissionError) or getattr(
                exc,
                "winerror",
                None,
            ) in (32, 33)
            if not sharing_violation or time.monotonic() >= deadline:
                raise
            time.sleep(delay_seconds)


def _copy_tree_for_publish(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _publish_stage(
    stage_root: Path,
    output_directory: Path,
    *,
    overwrite: bool,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    existing = existing_output_folders(output_directory)
    if existing and not overwrite:
        names = "、".join(path.name for path in existing)
        raise FileExistsError(f"输出目录已包含结果文件夹：{names}")

    for name in OUTPUT_FOLDERS:
        source = stage_root / name
        if not source.is_dir():
            raise RuntimeError(f"临时结果文件夹缺失：{source}")
        target = _known_target(output_directory, name)
        if target.exists():
            if not target.is_dir():
                raise RuntimeError(f"固定输出路径不是文件夹，拒绝覆盖：{target}")
        publishing = output_directory / f".{name}.publishing-{uuid.uuid4().hex}"
        try:
            _retry_sharing_violation(
                lambda: _copy_tree_for_publish(source, publishing)
            )
            if target.exists():
                _retry_sharing_violation(lambda: shutil.rmtree(target))
            _retry_sharing_violation(lambda: os.replace(publishing, target))
        finally:
            if publishing.exists():
                _retry_sharing_violation(lambda: shutil.rmtree(publishing))


def run_eis_job(
    input_path: Path | str,
    area_cm2: float,
    output_directory: Path | str,
    sample_label: str | None = None,
    *,
    overwrite: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run extraction, both Origin plots, verification, and final publication."""

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    source = Path(input_path).resolve()
    nox_files = list_nox_files(source)
    area = validate_area(area_cm2)
    destination = Path(output_directory).resolve()
    label = (sample_label or "").strip() or infer_sample_label(source)

    with tempfile.TemporaryDirectory(prefix="eis_plotter_") as temporary:
        stage = Path(temporary)
        data_dir = stage / "NOX处理结果"
        original_dir = stage / "原始EIS图"
        final_dir = stage / "最终EIS图"

        report("正在安全解析 NOX 并进行面积归一化…")
        extraction = process_inputs(source, data_dir, area)
        if not extraction:
            raise ValueError("没有从 NOX 文件中提取到 EIS 数据集。")
        extracted_sources = {str(item["source"]).casefold() for item in extraction}
        missing = [
            path.name
            for path in nox_files
            if path.name.casefold() not in extracted_sources
        ]
        if missing:
            raise ValueError("以下 NOX 未生成数据集：" + "、".join(missing))

        csv_files = sorted(data_dir.glob("*_area_normalized.csv"))
        csv_verification = [_verify_csv(path, area) for path in csv_files]

        report("正在 Origin 中生成原始面积归一化 Nyquist 图…")
        try:
            original = build_plot(
                data_dir,
                original_dir,
                label,
                intercept_zero=False,
                close_origin_on_exit=False,
            )
            report("正在生成左侧 Y=0 截距平移后的最终图…")
            final = build_plot(
                data_dir,
                final_dir,
                label,
                intercept_zero=True,
                close_origin_on_exit=False,
            )
        finally:
            close_origin()

        report("正在校验 OPJU、PNG 与归一化数据…")
        original_check = _verify_origin_output(
            Path(original["opju"]),
            Path(original["png"]),
        )
        final_check = _verify_origin_output(
            Path(final["opju"]),
            Path(final["png"]),
        )

        _publish_stage(stage, destination, overwrite=overwrite)

    original_output = destination / "原始EIS图"
    final_output = destination / "最终EIS图"
    return {
        "input": str(source),
        "area_cm2": area,
        "sample_label": label,
        "nox_files": len(nox_files),
        "datasets": len(extraction),
        "points": sum(int(item["point_count"]) for item in extraction),
        "data_directory": str(destination / "NOX处理结果"),
        "original_opju": str(original_output / original_check["opju"]),
        "original_png": str(original_output / original_check["png"]),
        "final_opju": str(final_output / final_check["opju"]),
        "final_png": str(final_output / final_check["png"]),
        "csv_verification": csv_verification,
        "original_verification": original_check,
        "final_verification": final_check,
    }
