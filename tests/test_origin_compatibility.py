from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from eis_app import gui
from eis_app.core import _retry_sharing_violation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ORIGIN_SCRIPT = PROJECT_ROOT / "scripts" / "plot_eis_origin.py"


class OriginVersionTests(unittest.TestCase):
    def origin_status_for(self, name: str) -> dict[str, object]:
        entries = [{"name": name, "version": "", "location": ""}]
        with (
            patch.object(gui, "_origin_uninstall_entries", return_value=entries),
            patch.object(gui, "origin_com_registered", return_value=True),
        ):
            return gui.origin_status()

    def test_origin_2021b_is_supported(self) -> None:
        status = self.origin_status_for("OriginPro 2021b")

        self.assertTrue(status["compatible"])
        self.assertEqual(status["years"], [2021])

    def test_origin_2020_is_rejected(self) -> None:
        status = self.origin_status_for("OriginPro 2020b")

        self.assertFalse(status["compatible"])
        self.assertEqual(status["years"], [2020])


class ProjectReleaseTests(unittest.TestCase):
    def test_saved_project_is_detached_before_build_plot_returns(self) -> None:
        script = ORIGIN_SCRIPT.read_text(encoding="utf-8-sig")
        save_index = script.index("op.save(str(opju))")
        detach_index = script.index("op.new()", save_index)
        return_index = script.index("return result", detach_index)

        self.assertLess(save_index, detach_index)
        self.assertLess(detach_index, return_index)

    def test_winerror_32_style_permission_failure_is_retried(self) -> None:
        attempts = 0

        def flaky_operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError("file is temporarily locked")
            return "ok"

        result = _retry_sharing_violation(
            flaky_operation,
            timeout_seconds=1.0,
            delay_seconds=0.0,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
