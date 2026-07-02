#!/usr/bin/env python3
"""Run the complete NOX -> normalized CSV -> Origin EIS workflow."""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path


def infer_sample_label(input_path: Path) -> str:
    files = sorted(input_path.glob("*.nox")) if input_path.is_dir() else [input_path]
    if not files:
        raise ValueError(f"no .nox files found in {input_path}")
    stem = files[0].stem
    match = re.match(r"([^-_]+)", stem)
    return match.group(1) if match else "EIS"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="NOX file or directory containing NOX files")
    parser.add_argument("--area-cm2", type=float, required=True, help="active electrode area A in cm^2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-label", help="label shown at the upper left; inferred from filename when omitted")
    args = parser.parse_args()

    if not math.isfinite(args.area_cm2) or args.area_cm2 <= 0:
        parser.error("--area-cm2 must be a finite positive number")
    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    sample_label = args.sample_label or infer_sample_label(input_path)
    script_dir = Path(__file__).resolve().parent
    data_dir = output_dir / "NOX处理结果"
    original_dir = output_dir / "原始EIS图"
    final_dir = output_dir / "最终EIS图"

    run([
        sys.executable,
        str(script_dir / "extract_nox.py"),
        str(input_path),
        "--area-cm2",
        format(args.area_cm2, ".15g"),
        "--output-dir",
        str(data_dir),
    ])
    base_plot_command = [
        sys.executable,
        str(script_dir / "plot_eis_origin.py"),
        str(data_dir),
        "--sample-label",
        sample_label,
    ]
    run(base_plot_command + ["--output-dir", str(original_dir)])
    run(base_plot_command + ["--output-dir", str(final_dir), "--intercept-zero"])

    print(f"NOX processing results: {data_dir}")
    print(f"Original EIS plot: {original_dir}")
    print(f"Final EIS plot: {final_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
