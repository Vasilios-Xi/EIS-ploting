"""Run the complete application workflow against a real NOX file or folder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eis_app.core import run_eis_job


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("area_cm2", type=float)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--sample-label", default="EIS EXE Smoke Test")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_eis_job(
        args.input,
        args.area_cm2,
        args.output_directory,
        args.sample_label,
        overwrite=True,
        progress=lambda message: print(message, flush=True),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
