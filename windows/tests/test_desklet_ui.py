"""Regression coverage for the Windows floating desklet and wrapped text layout."""

import sys
import tempfile
import time
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

    def test_verified_groq_key_starts_background_app_list_and_save_closes_immediately(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QDialog
        from setup_app import SetupDialog
        from step_desklet.core.config import read_env_keys

        requests = []
        dialog = SetupDialog()
        self.addCleanup(dialog.close)
        dialog.name.setText("Test profile")
        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData("groq"))
        with (
            patch("setup_app.set_windows_startup"),
            patch("setup_app.QMessageBox.warning", side_effect=AssertionError("Unexpected setup warning")),
            patch("step_desklet.win11.request_process.DeskletRequest.start_request",
                  autospec=True, side_effect=lambda process, payload: requests.append((process, payload))),
        ):
            dialog.groq_key.setText("test-groq-key")
            QTest.qWait(550)
            probe, payload = requests[0]
            self.assertEqual(payload["operation"], "check_provider_key")
            self.assertEqual(read_env_keys()["GROQ_API_KEY"], "")
            probe.outcome.emit("response", "Verified")
            build, payload = requests[1]
            self.assertEqual(payload["operation"], "rebuild_app_catalog")
            self.assertFalse(payload["check_key"])
            self.assertIs(dialog._catalog_jobs.request, build)
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            self.assertEqual(read_env_keys()["GROQ_API_KEY"], "test-groq-key")
            self.assertIs(dialog._catalog_jobs.request, build)
            self.assertIsNone(dialog._catalog_jobs.draft)
            self.assertEqual(len(requests), 2)
            build.outcome.emit("response", "App list rebuilt.")

    def test_memory_slider_loads_and_saves_conversation_turns(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QDialogButtonBox, QLabel, QSlider

        from setup_app import SetupDialog
        from step_desklet.core.config import get_settings_file, load_settings_config, write_json_file

        write_json_file(get_settings_file(), {
            "name": "Test profile", "defaultProvider": "lmstudio", "memoryTurns": 18,
        })
        dialog = SetupDialog()
        self.addCleanup(dialog.close)
        dialog.show()
        slider = dialog.memory_turns
        self.assertIsInstance(slider, QSlider)
        self.assertEqual((slider.minimum(), slider.maximum(), slider.value()), (4, 30, 18))
        turns = dialog.findChild(QLabel, "StudioMemoryTurns")
        self.assertEqual(turns.text(), "18 turns")
        slider.setFocus()
        QTest.keyClick(slider, Qt.Key.Key_Right)
        self.assertEqual(turns.text(), "19 turns")
        self.assertEqual(load_settings_config()["memoryTurns"], 18)
        with patch("setup_app.set_windows_startup"):
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        self.assertEqual(load_settings_config()["memoryTurns"], 19)
        reopened = SetupDialog()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.memory_turns.value(), 19)

    def test_settings_studio_has_a_zylo_icon_and_navigates_to_a_missing_key(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QPushButton

        from setup_app import SetupDialog, save_setup

        dialog = SetupDialog(show_desklet=lambda theme: None)
        self.addCleanup(dialog.close)
        dialog.show()
        self.assertEqual(dialog.windowType(), Qt.WindowType.Window)
        self.assertFalse(dialog.windowIcon().isNull())
        self.assertEqual(dialog.windowTitle(), "liNi Settings")
        navigation = {button.text(): button for button in dialog.findChildren(QPushButton, "StudioNav")}
        self.assertEqual(list(navigation), ["General", "Providers", "Appearance", "System", "Voice"])
        navigation["Appearance"].click()
        self.assertTrue(dialog.color_wheel.isVisible())
        dialog.gradient_preset_combo.setCurrentIndex(dialog.gradient_preset_combo.findData("Editor's Choice"))
        self.assertEqual((dialog.gradient_start, dialog.gradient_mid, dialog.gradient_end),
                         ("#fb6e11", "#f41a49", "#9552fc"))
        self.assertFalse(dialog.key.isVisible())
        dialog.name.setText("Test profile")
        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData("nim"))
        with patch("setup_app.QMessageBox.warning") as warning:
            self.assertFalse(save_setup(dialog))
        warning.assert_called_once()
        self.assertTrue(navigation["Providers"].isChecked())
        self.assertTrue(dialog.key.isVisible())

    def test_backup_keys_are_optional_masked_and_removable_in_settings(self):
        from PyQt6.QtWidgets import QLineEdit

        from setup_app import SetupDialog, save_setup
        from step_desklet.core.config import read_env_keys, write_env_keys

        write_env_keys(nvidia_key="nvidia-primary", groq_key="groq-primary")
        dialog = SetupDialog()
        self.addCleanup(dialog.close)
        dialog.name.setText("Test profile")
        self.assertEqual(dialog.nvidia_backup_key.placeholderText(), "Enter key")
        self.assertFalse(dialog.remove_groq_backup.isEnabled())
        dialog.nvidia_backup_key.setText("nvidia-backup")
        dialog.groq_backup_key.setText("groq-backup")
        with patch("setup_app.set_windows_startup"):
            self.assertTrue(save_setup(dialog))
            reopened = SetupDialog()
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.nvidia_backup_key.echoMode(), QLineEdit.EchoMode.Password)
            self.assertEqual(reopened.nvidia_backup_key.text(), "")
            self.assertEqual(reopened.groq_backup_key.placeholderText(), "Enter replacement")
            self.assertTrue(save_setup(reopened))
            self.assertEqual(read_env_keys()["NVIDIA_BACKUP_API_KEY"], "nvidia-backup")
            reopened.remove_nvidia_backup.setChecked(True)
            self.assertFalse(reopened.nvidia_backup_key.isEnabled())
            self.assertTrue(save_setup(reopened))

        keys = read_env_keys()
        self.assertEqual(keys["NVIDIA_API_KEY"], "nvidia-primary")
        self.assertEqual(keys["GROQ_API_KEY"], "groq-primary")
        self.assertEqual(keys["NVIDIA_BACKUP_API_KEY"], "")
        self.assertEqual(keys["GROQ_BACKUP_API_KEY"], "groq-backup")

    def test_local_date_is_saved_on_startup_and_refreshed_after_rollover(self):
        from datetime import date

        from step_desklet.core import config
        from step_desklet.win11.tray_app import ZyloWin11Controller

        path = config.get_config_dir() / "local-date.json"
        self.assertFalse(path.exists())
        with (
            patch.object(config, "date") as clock,
            patch("step_desklet.win11.tray_app.set_windows_startup"),
            patch.object(ZyloWin11Controller, "open_setup"),
            patch("step_desklet.win11.tray_app.DesktopFocusMonitor.start"),
        ):
            clock.today.return_value = date(2025, 12, 31)
            self.controller.start()
            self.assertEqual(config.read_json_file(path), {"date": "2025-12-31"})
            self.assertTrue(self.controller._local_date_timer.isActive())
            with patch.object(config, "write_json_file") as write:
                self.controller._local_date_timer.timeout.emit()
                write.assert_not_called()

            clock.today.return_value = date(2026, 1, 1)
            self.controller._local_date_timer.timeout.emit()
            self.assertEqual(config.read_json_file(path), {"date": "2026-01-01"})

            clock.today.return_value = date(2026, 1, 2)
            self.controller.shutdown_cleanup()
            self.assertFalse(self.controller._local_date_timer.isActive())
            self.assertEqual(config.read_json_file(path), {"date": "2026-01-02"})

            clock.today.return_value = date(2026, 2, 1)
            relaunched = ZyloWin11Controller(self.app)
            self.addCleanup(relaunched.shutdown_cleanup)
            relaunched.start()
            self.assertEqual(config.read_json_file(path), {"date": "2026-02-01"})

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

    def test_prompt_outline_keeps_its_end_colors_at_fractional_scaling(self):
        from math import ceil

        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtWidgets import QWidget

        self.desklet.apply_theme({
            "accentMode": "gradient",
            "gradientStart": "#ff0000",
            "gradientMid": "#00ff00",
            "gradientEnd": "#0000ff",
        })
        shell = self.desklet.input_shell
        shell.resize(260, 34)
        for scale in (1, 1.25, 1.5, 2):
            with self.subTest(scale=scale):
                pixmap = QPixmap(ceil(shell.width() * scale), ceil(shell.height() * scale))
                pixmap.setDevicePixelRatio(scale)
                pixmap.fill(Qt.GlobalColor.transparent)
                shell.render(pixmap, flags=QWidget.RenderFlag(0))
                image = pixmap.toImage()
                middle = image.height() // 2
                for row in (middle - 3, middle, middle + 3):
                    left = image.pixelColor(0, row)
                    right = image.pixelColor(image.width() - 1, row)
                    self.assertGreater(left.red(), 200)
                    self.assertGreater(right.blue(), 200)
                    self.assertLess(left.green(), 30)
                    self.assertLess(right.green(), 30)
                self.assertGreater(image.pixelColor(image.width() // 2, 0).green(), 200)

    def _wait_for(self, condition, timeout=8):
        from PyQt6.QtTest import QTest

        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition(), "Condition did not arrive before the timeout")

    def test_stop_cancels_model_io_and_a_powershell_process_tree(self):
        import ctypes
        import json
        from ctypes import wintypes
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Event, Thread

        from step_desklet.core.config import get_conversation_file
        from step_desklet.core.worker import _ps_quote

        started = Event()
        disconnected = Event()
        pid_file = get_conversation_file().with_name("command-pids.txt")
        chat_store = self.desklet.chat_store
        command = (
            "$child = Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 60'"
            " -PassThru -NoNewWindow; "
            f"[IO.File]::WriteAllText({_ps_quote(str(pid_file))}, \"$PID,$($child.Id)\"); "
            "Wait-Process -Id $child.Id"
        )

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                prompt = request["messages"][-1]["content"]
                if prompt == "Stalled request":
                    started.set()
                    self.connection.settimeout(8)
                    try:
                        if not self.connection.recv(1):
                            disconnected.set()
                    except (ConnectionResetError, ConnectionAbortedError):
                        disconnected.set()
                    return
                if prompt == "Start command":
                    answer = json.dumps({"mode": "run", "command": command, "reason": "Cancellation test"})
                else:
                    answer = json.dumps({"mode": "final", "message": "Fresh response."})
                body = json.dumps({"choices": [{"message": {"content": answer}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), Model))
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.shutdown)
        self.enterContext(patch.dict("os.environ", {
            "LMSTUDIO_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
            "LMSTUDIO_MODEL": "test-model",
        }))
        self.desklet._provider = "lmstudio"
        self.desklet.prompt_input.setText("Stalled request")
        self.desklet.send_btn.click()
        self._wait_for(started.is_set)

        stop_started = time.monotonic()
        self.desklet.clear_btn.click()
        self.assertLess(time.monotonic() - stop_started, 0.5)
        self.assertTrue(self.desklet.prompt_input.isEnabled())
        self.assertFalse(self.desklet.status_label.isVisible())
        self.assertFalse(self.desklet._glow_timer.isActive())
        self.desklet.prompt_input.setText("Next request")
        self.desklet.send_btn.click()
        self._wait_for(disconnected.is_set)
        self._wait_for(lambda: self.desklet.response_label.text() == "Fresh response.")
        chat = chat_store.active()
        history = chat_store.history(chat, 8)
        self.assertEqual(history, [
            {"role": "user", "content": "Next request"},
            {"role": "assistant", "content": "Fresh response."},
        ])

        self.desklet.prompt_input.setText("Start command")
        self.desklet.send_btn.click()
        self._wait_for(lambda: pid_file.exists() and "," in pid_file.read_text())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        pids = [int(pid) for pid in pid_file.read_text().split(",")]
        handles = [kernel.OpenProcess(0x100000, False, pid) for pid in pids]
        try:
            self.assertTrue(all(handles))
            self.assertTrue(all(kernel.WaitForSingleObject(handle, 0) == 0x102 for handle in handles))
            self.desklet.clear_btn.click()
            self.assertTrue(self.desklet.prompt_input.isEnabled())
            self._wait_for(lambda: all(kernel.WaitForSingleObject(handle, 0) == 0 for handle in handles), timeout=1)
        finally:
            for handle in handles:
                if handle:
                    kernel.CloseHandle(handle)
        self.assertEqual(chat_store.history(chat, 8), history)

    def test_stop_dismisses_a_pending_administrator_tool_request(self):
        from step_desklet.core.config import get_settings_file, write_json_file

        write_json_file(get_settings_file(), {"allowAdminToolRequests": True})
        with patch("step_desklet.win11.desklet.launch_admin_tool") as launch:
            self.desklet._set_busy(True)
            self.desklet._on_admin_tool_requested("task-manager")
            prompt = self.desklet._admin_prompt
            self.assertIsNotNone(prompt)
            self.assertTrue(prompt.isVisible())
            self.assertFalse(prompt.isModal())
            self.desklet.clear_btn.click()
            self.assertIsNone(self.desklet._admin_prompt)
            self.assertFalse(prompt.isVisible())
            self.assertTrue(self.desklet.prompt_input.isEnabled())
            launch.assert_not_called()

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
            patch.object(focus_monitor, "_window_was_hidden_or_minimized", return_value=True),
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

    def test_taskbar_handoff_leaves_the_desklet_behind_open_apps(self):
        from step_desklet.win11 import focus_monitor

        monitor = self.controller.focus_monitor
        with (
            patch.object(focus_monitor, "_get_foreground_info", return_value=(41, "Codex", 400)) as foreground,
            patch.object(focus_monitor, "_window_was_hidden_or_minimized", return_value=False) as app_left,
            patch.object(self.desklet, "restore_above_wallpaper") as restore,
            patch.object(self.desklet, "bring_to_desktop") as raise_desklet,
        ):
            monitor._evaluate_foreground()
            restore.reset_mock()
            for event in ((42, "Shell_TrayWnd", 501), (0, "", 0), (43, "Shell_SecondaryTrayWnd", 501)):
                foreground.return_value = event
                monitor._evaluate_foreground()
            restore.assert_not_called()
            raise_desklet.assert_not_called()
            app_left.assert_called_with(41)
            foreground.return_value = (44, "Chrome_WidgetWin_1", 600)
            monitor._evaluate_foreground()
            restore.assert_called_once_with()
            raise_desklet.assert_not_called()

    def test_show_desktop_recovers_after_apps_minimize_while_the_taskbar_has_focus(self):
        from step_desklet.win11 import focus_monitor

        monitor = self.controller.focus_monitor
        with (
            patch.object(focus_monitor, "_get_foreground_info", return_value=(41, "Codex", 400)) as foreground,
            patch.object(focus_monitor, "_window_was_hidden_or_minimized", return_value=True),
        ):
            monitor._evaluate_foreground()
            self.desklet.showMinimized()
            foreground.return_value = (42, "Shell_TrayWnd", 501)
            monitor._evaluate_foreground()
        self.assertTrue(self.desklet.isVisible())
        self.assertFalse(self.desklet.isMinimized())

    def test_show_desklet_previews_gradient_and_cancel_restores_saved_colors(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QDialogButtonBox, QPushButton
        from step_desklet.core.config import get_settings_file, load_settings_config, write_json_file

        settings_file = get_settings_file()
        write_json_file(settings_file, {"name": "Saved", "defaultProvider": "lmstudio", "accent": "#112233"})
        self.desklet.reload_settings()
        saved_bytes = settings_file.read_bytes()
        custom = ("#314159", "#267384", "#bb92ac")

        def edit_and_preview():
            self.controller.open_setup()
            dialog = self.controller._settings_window
            dialog._select_section("Appearance")
            dialog.accent_mode_combo.setCurrentIndex(dialog.accent_mode_combo.findData("gradient"))
            for button, color in zip((dialog.grad_start_btn, dialog.grad_mid_btn, dialog.grad_end_btn), custom):
                button.click()
                dialog.wheel_hex_input.selectAll()
                QTest.keyClicks(dialog.wheel_hex_input, color)
            buttons = dialog.findChild(QDialogButtonBox)
            dialog.findChild(QPushButton, "StudioShowDesklet").click()
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
