from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from eis_app.core import infer_sample_label, list_nox_files, validate_area
from scripts.extract_nox import normalize_dataset
from scripts.plot_eis_origin import (
    common_axis,
    series_info,
    shift_to_left_y0_intercept,
)


def make_dataset() -> dict[str, list[float]]:
    real = [1.0, 2.0, 3.0]
    imaginary = [-0.2, 0.5, 1.0]
    return {
        "Frequency": [1000.0, 100.0, 10.0],
        "H_Real": real,
        "H_Imaginary": imaginary,
        "H_Modulus": [
            math.hypot(x_value, y_value)
            for x_value, y_value in zip(real, imaginary)
        ],
        "H_Argument": [
            math.atan2(y_value, x_value)
            for x_value, y_value in zip(real, imaginary)
        ],
        "H_Phase": [
            math.degrees(math.atan2(y_value, x_value))
            for x_value, y_value in zip(real, imaginary)
        ],
        "Time": [0.0, 1.0, 2.0],
    }


class NormalizationTests(unittest.TestCase):
    def test_area_normalization_preserves_nova_imaginary_sign(self) -> None:
        rows, qa = normalize_dataset(make_dataset(), 2.5)

        self.assertEqual(
            [row["Z_real_area_Ohm_cm2"] for row in rows],
            [2.5, 5.0, 7.5],
        )
        self.assertEqual(
            [row["minus_Z_imag_area_Ohm_cm2"] for row in rows],
            [-0.5, 1.25, 2.5],
        )
        self.assertEqual(qa["area_normalization_max_error"], 0.0)
        self.assertTrue(qa["all_values_finite"])

    def test_area_must_be_positive_and_finite(self) -> None:
        self.assertEqual(validate_area(0.25), 0.25)
        for value in (0, -1, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_area(value)


class PlotPreparationTests(unittest.TestCase):
    def test_leftmost_y_zero_crossing_is_interpolated_and_shifted(self) -> None:
        x_values, y_values = shift_to_left_y0_intercept(
            [0.0, 1.0, 2.0],
            [-0.5, 0.5, 1.0],
        )

        self.assertEqual(x_values, [0.0, 0.5, 1.5])
        self.assertEqual(y_values, [0.0, 0.5, 1.0])

    def test_common_axis_uses_one_range_for_x_and_y(self) -> None:
        low, high, step = common_axis(
            [([0.0, 1.3], [-0.1, 0.9]), ([0.2, 2.1], [0.0, 1.8])],
            intercept_zero=False,
        )

        self.assertLessEqual(low, -0.1)
        self.assertGreaterEqual(high, 2.1)
        self.assertGreater(step, 0)

    def test_oxygen_series_are_labeled_and_sorted_numerically(self) -> None:
        five = series_info(Path("sample-5O2_eis_01_area_normalized.csv"))
        twenty = series_info(Path("sample-20O2_eis_01_area_normalized.csv"))

        self.assertLess(five[0], twenty[0])
        self.assertEqual(five[1], r"5% O\-(2)")
        self.assertEqual(five[2], "5O2")


class InputTests(unittest.TestCase):
    def test_folder_input_and_sample_label_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SOFC-5O2.nox").touch()
            (root / "SOFC-20O2.nox").touch()
            (root / "ignore.txt").touch()

            files = list_nox_files(root)

            self.assertEqual(len(files), 2)
            self.assertEqual(infer_sample_label(root), "SOFC")


if __name__ == "__main__":
    unittest.main()
