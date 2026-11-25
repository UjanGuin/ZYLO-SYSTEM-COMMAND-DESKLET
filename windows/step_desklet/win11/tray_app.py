"""Windows 11 System Tray companion and setup lifecycle manager for ZYLO."""

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

from step_desklet.core.config import (
    PROVIDER_LABELS,
    get_accent_config,
    get_env_file,
    get_settings_file,
    is_windows_startup_enabled,
    read_json_file,
    refresh_local_date,
    set_windows_startup,
)
from step_desklet.core.skills import ensure_skills_directory
from step_desklet.win11.desklet import ZyloDeskletWindow
from step_desklet.win11.focus_monitor import DesktopFocusMonitor


def create_zylo_tray_icon(settings: dict | None = None) -> QIcon:
    """Render a crisp 64x64 ZYLO glyph icon for the Windows 11 tray."""
    cfg = get_accent_config(settings)
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0.0, QColor("#1b132b"))
    grad.setColorAt(1.0, QColor("#2c1d48"))
    painter.setBrush(grad)

    if cfg["mode"] == "gradient":
        rim = QLinearGradient(0, 0, 64, 64)
        rim.setColorAt(0.0, QColor(cfg["gradientStart"]))
        rim.setColorAt(0.5, QColor(cfg["gradientMid"]))
        rim.setColorAt(1.0, QColor(cfg["gradientEnd"]))
        painter.setPen(QPen(rim, 3.5))
    else:
        painter.setPen(QPen(QColor(cfg["accent"]), 3))
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)

    painter.setPen(
        QPen(
            QColor("#dccaff"),
            5,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.drawLine(20, 21, 44, 21)
    painter.drawLine(44, 21, 20, 43)
    painter.drawLine(20, 43, 44, 43)
    painter.end()
    return QIcon(pixmap)


class ZyloWin11Controller:
    """Coordinates the PyQt6 Acrylic desklet, Win11 focus monitor, tray icon, and setup dialog."""

    def __init__(self, app: QApplication):
        self.app = app
        self.app.setQuitOnLastWindowClosed(False)

        self.desklet = ZyloDeskletWindow()
        self.desklet.request_setup.connect(self.open_setup)
        self.desklet.provider_changed.connect(self._sync_menu_labels)
        self.desklet.lock_changed.connect(self._sync_menu_labels)

        self.focus_monitor = DesktopFocusMonitor(lambda: int(self.desklet.winId()))
        self.focus_monitor.desktop_focus_changed.connect(self._on_desktop_focus)

        self._desklet_enabled = True
        self._is_shutting_down = False
        self._settings_window = None
        self._local_date_timer = QTimer(self.desklet)
        self._local_date_timer.setInterval(60_000)
        self._local_date_timer.timeout.connect(refresh_local_date)
        self.app.aboutToQuit.connect(self.shutdown_cleanup)
        self._build_tray()

    def _build_tray(self) -> None:
        settings = read_json_file(get_settings_file())

        icon = create_zylo_tray_icon(settings)
        self.tray = QSystemTrayIcon(icon, self.app)
        self.tray.setToolTip("ZYLO Desktop Assistant · Windows 11")
        self.tray.activated.connect(self._on_tray_activated)

        menu = QMenu()
        self.toggle_action = QAction("Hide Desklet", menu)
        self.toggle_action.triggered.connect(self.toggle_desklet_visibility)
        menu.addAction(self.toggle_action)

        self.setup_action = QAction("ZYLO Settings", menu)
        self.setup_action.triggered.connect(self.open_setup)
        menu.addAction(self.setup_action)

        menu.addSeparator()

        self.provider_action = QAction("Cycle Provider (NVIDIA / Groq / LM Studio)", menu)
        self.provider_action.triggered.connect(self.desklet.toggle_provider)
        menu.addAction(self.provider_action)

        self.lock_action = QAction("Lock Panel Position", menu)
        self.lock_action.triggered.connect(self.desklet.toggle_lock)
        menu.addAction(self.lock_action)

        self.reset_layout_action = QAction("Reset Position & Size", menu)
        self.reset_layout_action.triggered.connect(self.desklet.reset_layout)
        menu.addAction(self.reset_layout_action)

        self.open_skills_action = QAction("Open Skills Folder (zylo_skills/)", menu)
        self.open_skills_action.triggered.connect(self._open_skills_folder)
        menu.addAction(self.open_skills_action)

        self.manage_skills_action = QAction("Manage Skills, Trust & History…", menu)
        self.manage_skills_action.triggered.connect(self.open_skills_manager)
        menu.addAction(self.manage_skills_action)

        self.clear_memory_action = QAction("Reset Conversation Memory", menu)
        self.clear_memory_action.triggered.connect(self._reset_memory)
        menu.addAction(self.clear_memory_action)

        self.startup_action = QAction("Launch at Windows Sign-in", menu)
        self.startup_action.setCheckable(True)
        self.startup_action.setChecked(is_windows_startup_enabled())
        self.startup_action.triggered.connect(self._toggle_startup)
        menu.addAction(self.startup_action)

        menu.addSeparator()

        quit_action = QAction("Quit ZYLO", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self._sync_menu_labels()
        self.tray.show()

    def _sync_menu_labels(self, *_args) -> None:
        next_label = PROVIDER_LABELS[self.desklet.next_provider()]
        self.provider_action.setText(f"Switch Provider → {next_label}")
        self.lock_action.setText("Unlock Panel Position" if self.desklet._locked else "Lock Panel Position")

    def _reset_memory(self) -> None:
        from step_desklet.core.config import get_conversation_file

        self.desklet.clear_all()
        conv_file = get_conversation_file()
        try:
            if conv_file.exists():
                conv_file.unlink()
        except OSError:
            pass

    def _open_skills_folder(self) -> None:
        skills_dir = ensure_skills_directory()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(skills_dir)))

    def _toggle_startup(self, checked: bool) -> None:
        ok = set_windows_startup(checked)
        if not ok:
            self.startup_action.setChecked(is_windows_startup_enabled())

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_desklet()

    def _on_desktop_focus(self, focused: bool) -> None:
        if not self._desklet_enabled or self._is_shutting_down:
            return
        if focused and self.desklet.isMinimized():
            self.desklet.showNormal()
        self.desklet.restore_above_wallpaper()

    def toggle_desklet_visibility(self) -> None:
        if self._desklet_enabled:
            self._desklet_enabled = False
            self.desklet.hide()
            self.toggle_action.setText("Show Desklet")
        else:
            self.show_desklet()

    def show_desklet(self) -> None:
        self._desklet_enabled = True
        self.desklet.bring_to_desktop()
        self.desklet.activateWindow()
        self.desklet.prompt_input.setFocus()
        self.toggle_action.setText("Hide Desklet")

    def preview_desklet(self, theme: dict[str, str]) -> None:
        self.desklet.apply_theme(theme)
        self.show_desklet()

    def open_setup(self) -> None:
        from setup_app import SetupDialog

        if self._is_shutting_down:
            return
        if self._settings_window is None:
            window = SetupDialog(show_desklet=self.preview_desklet)
            window.finished.connect(self._settings_finished)
            self._settings_window = window
        self._settings_window.showNormal()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    def _settings_finished(self, result: int) -> None:
        window = self._settings_window
        self._settings_window = None
        window.deleteLater()
        if self._is_shutting_down:
            return
        if result != QDialog.DialogCode.Accepted:
            self.desklet.apply_theme()
            return

        self.desklet.reload_settings()
        settings = read_json_file(get_settings_file())
        icon = create_zylo_tray_icon(settings)
        self.tray.setIcon(icon)
        self.startup_action.setChecked(is_windows_startup_enabled())
        self._sync_menu_labels()
        self.show_desklet()

    def open_skills_manager(self) -> None:
        from setup_app import SkillManagerDialog

        self.focus_monitor.set_paused(True)
        dlg = SkillManagerDialog(parent=self.desklet)
        dlg.exec()
        self.focus_monitor.set_paused(False)

    def start(self, force_setup: bool = False) -> None:
        refresh_local_date()
        self._local_date_timer.start()
        settings = read_json_file(get_settings_file())
        needs_first_run = force_setup or not (settings.get("name") and get_env_file().exists())
        if self.startup_action.isChecked():
            set_windows_startup(True)
        self.desklet.show()
        self.focus_monitor.start()
        if needs_first_run:
            self.open_setup()

    def shutdown_cleanup(self) -> None:
        """Stop desktop monitoring and active requests before destroying the desklet."""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self._local_date_timer.stop()
        refresh_local_date()
        self.focus_monitor.stop()
        if self._settings_window is not None:
            self._settings_window.reject()
        if jobs := getattr(self.app, "_zylo_app_list_jobs", None):
            jobs.cancel()
        self.desklet.shutdown()
        self.desklet.close()
        self.desklet.deleteLater()
        self.tray.hide()

    def quit(self) -> None:
        self.shutdown_cleanup()
        self.app.quit()
