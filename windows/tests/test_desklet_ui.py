"""Regression coverage for the Windows floating desklet and wrapped text layout."""

import sys
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "Windows desklet")
class DeskletUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from step_desklet.core.config import get_layout_file, write_json_file
        from step_desklet.win11.tray_app import ZyloWin11Controller

        profile = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict("os.environ", {"APPDATA": profile}))
        write_json_file(get_layout_file(), {"x": 40, "y": 40, "width": 317, "height": 108, "locked": True})
        self.controller = ZyloWin11Controller(self.app)
        self.addCleanup(self.controller.shutdown_cleanup)
        self.desklet = self.controller.desklet
        self.desklet.show()
        self.app.processEvents()

    def _assert_prompt_bottom_spacing(self):
        expected = self.desklet.card.layout().contentsMargins().bottom()
        actual = self.desklet.card.height() - self.desklet.input_shell.geometry().bottom() - 1
        self.assertEqual(actual, expected)

    def test_wrapped_status_and_response_keep_the_prompt_inside_the_rim(self):
        self._assert_prompt_bottom_spacing()
        self.desklet._on_worker_status("Thinking.")
        self.app.processEvents()
        short_height = self.desklet.height()
        self.desklet._on_worker_status(
            "Executing: Open Task Manager to check if CPU temperature is available for this system"
        )
        self.app.processEvents()
        self.assertGreater(self.desklet.height(), short_height)
        status = self.desklet.status_label
        self.assertGreaterEqual(status.height(), status.heightForWidth(status.width()))
        self._assert_prompt_bottom_spacing()

        self.desklet._on_worker_response("This is a long reply with wrapped text.\n" * 140)
        self.app.processEvents()
        self.assertGreater(self.desklet.message_area.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(self.desklet.height(), self.desklet.screen().availableGeometry().height())
        self._assert_prompt_bottom_spacing()
        self.desklet.clear_all()
        self.app.processEvents()
        self.assertTrue(self.desklet.message_area.isHidden())
        self._assert_prompt_bottom_spacing()

    def test_foreground_changes_leave_desklet_open_and_respect_tray_hide(self):
        from step_desklet.win11 import focus_monitor

        monitor = self.controller.focus_monitor
        with patch.object(focus_monitor, "_get_foreground_info", return_value=(42, "Notepad", 200)) as fg:
            monitor._evaluate_foreground()
            self.assertTrue(self.desklet.isVisible())
            fg.return_value = (43, "Progman", 201)
            self.desklet.showMinimized()
            monitor._evaluate_foreground()
            self.assertTrue(self.desklet.isVisible())
            self.assertFalse(self.desklet.isMinimized())
            self.controller.toggle_desklet_visibility()
            fg.return_value = (42, "Notepad", 200)
            monitor._evaluate_foreground()
            fg.return_value = (43, "Progman", 201)
            monitor._evaluate_foreground()
            self.assertFalse(self.desklet.isVisible())
            self.controller.toggle_desklet_visibility()
            self.assertTrue(self.desklet.isVisible())

    def test_window_close_recovers_desklet_without_desktop_foreground(self):
        from step_desklet.win11 import desklet, focus_monitor

        monitor = self.controller.focus_monitor
        with (
            patch.object(focus_monitor, "_get_foreground_info", return_value=(11, "Notepad", 400)) as foreground,
            patch.object(desklet, "wallpaper_covering_window", side_effect=[None, 300, 300]) as probe,
            patch.object(desklet, "show_window_above_wallpaper") as restore,
        ):
            monitor._evaluate_foreground()
            restore.assert_not_called()
            foreground.return_value = (0, "", 0)
            monitor._evaluate_foreground()
            restore.assert_called_once_with(int(self.desklet.winId()), 300)
            self.desklet.hide()
            foreground.return_value = (12, "CabinetWClass", 401)
            monitor._evaluate_foreground()
            self.assertTrue(self.desklet.isVisible())
            self.assertEqual(restore.call_count, 2)
            self.controller.toggle_desklet_visibility()
            monitor._evaluate_foreground()
            self.assertFalse(self.desklet.isVisible())
            self.assertEqual(probe.call_count, 3)

    def test_show_desklet_previews_gradient_and_cancel_restores_saved_colors(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QDialogButtonBox
        from step_desklet.core.config import get_settings_file, load_settings_config, write_json_file

        settings_file = get_settings_file()
        write_json_file(settings_file, {"name": "Saved", "defaultProvider": "lmstudio", "accent": "#112233"})
        self.desklet.reload_settings()
        saved_bytes = settings_file.read_bytes()
        custom = ("#314159", "#267384", "#bb92ac")

        def edit_and_preview():
            self.controller.open_setup()
            dialog = self.controller._settings_window
            dialog.accent_mode_combo.setCurrentIndex(dialog.accent_mode_combo.findData("gradient"))
            for button, color in zip((dialog.grad_start_btn, dialog.grad_mid_btn, dialog.grad_end_btn), custom):
                button.click()
                dialog.wheel_hex_input.selectAll()
                QTest.keyClicks(dialog.wheel_hex_input, color)
            buttons = dialog.findChild(QDialogButtonBox)
            next(button for button in buttons.buttons() if button.text() == "Show Desklet").click()
            self.assertEqual(tuple(color.name() for color in self.desklet.card.colors), custom)
            self.assertEqual(settings_file.read_bytes(), saved_bytes)
            self.assertTrue(dialog.isVisible())
            return buttons

        with patch("setup_app.set_windows_startup", return_value=True):
            edit_and_preview().button(QDialogButtonBox.StandardButton.Cancel).click()
            self.assertEqual(tuple(color.name() for color in self.desklet.card.colors), ("#112233",) * 3)
            self.assertEqual(settings_file.read_bytes(), saved_bytes)
            edit_and_preview().button(QDialogButtonBox.StandardButton.Save).click()

        saved = load_settings_config()
        self.assertEqual(tuple(saved[f"gradient{stop}"] for stop in ("Start", "Mid", "End")), custom)
        self.assertEqual(tuple(color.name() for color in self.desklet.card.colors), custom)
