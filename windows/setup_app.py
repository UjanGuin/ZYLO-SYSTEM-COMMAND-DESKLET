#!/usr/bin/env python3
"""liNi first-run and settings launcher (cross-platform Linux GNOME & Windows 11 26H2)."""

import math
import re
import subprocess
import sys

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QConicalGradient,
    QDesktopServices,
    QIcon,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QKeySequenceEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from step_desklet.core.config import (
    ACCENTS,
    DEFAULT_FILE_SEARCH_LEVELS,
    DEFAULT_LMSTUDIO_BASE_URL,
    FREELLMAPI_BASE_URL,
    FREELLMAPI_ROUTES,
    GRADIENT_PRESETS,
    IS_WINDOWS,
    MAX_FILE_SEARCH_LEVEL_INPUT,
    MAX_MEMORY_TURNS,
    MIN_MEMORY_TURNS,
    PROVIDER_LABELS,
    enabled_providers,
    ensure_config_dir,
    get_accent_config,
    get_memory_turns,
    get_settings_file,
    is_windows_startup_enabled,
    load_settings_config,
    read_env_keys,
    read_provider,
    set_windows_startup,
    write_env_keys,
    write_json_file,
    write_provider,
)
from step_desklet.core.installed_apps import catalog_names, load_app_catalog
from step_desklet.core.assistant_context import weather_coordinates
from step_desklet.core.skills import (
    clear_skill_history,
    discover_skills,
    ensure_skills_directory,
    read_skill_history,
    set_skill_enabled,
    trust_skill,
    untrust_skill,
)
from step_desklet.core.worker import list_lmstudio_models, normalize_lmstudio_base_url
from step_desklet.win11.branding import set_zylo_app_user_model_id, zylo_app_icon
from step_desklet.win11.dwm import use_dark_title_bar

API_KEY_URL = "https://build.nvidia.com/"
GROQ_API_KEY_URL = "https://console.groq.com/keys"
TAVILY_API_KEY_URL = "https://app.tavily.com/home"
LMSTUDIO_DOCS_URL = "https://lmstudio.ai/docs/app/api"

SETTINGS_STYLE = """
    QDialog#ZyloSettings {
        background: #12131c; color: #edf0fa;
        font-family: "Segoe UI"; font-size: 13px;
    }
    QLabel { color: #e9ecf7; }
    QLabel#StudioMuted { color: #a5adc2; }
    QLabel#StudioBrand { color: #f6f0ff; font-size: 19px; font-weight: 750; }
    QLabel#StudioTitle { color: #f7f3ff; font-size: 33px; font-weight: 650; }
    QLabel#StudioCardTitle { color: #f2efff; font-size: 17px; font-weight: 650; }
    QLabel#StudioInfoIcon { color: #baa9ff; font-size: 15px; }
    QFrame#StudioSidebar { background: #191a26; border-right: 1px solid #2d3042; }
    QFrame#StudioFooter { background: #161822; border-top: 1px solid #2d3042; }
    QFrame#StudioCard { background: #1b1e2b; border: 1px solid #303447; border-radius: 14px; }
    QScrollArea, QStackedWidget, QWidget#StudioScrollContent {
        background: transparent; border: none;
    }
    QScrollBar:vertical { background: transparent; width: 8px; margin: 3px 0; }
    QScrollBar::handle:vertical { background: #464963; border-radius: 4px; min-height: 24px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QLineEdit, QComboBox, QSpinBox {
        color: #f5f4fe; background: #141621; border: 1px solid #393d53;
        border-radius: 8px; padding: 8px 10px; min-height: 18px;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #ad9afa; }
    QLineEdit:disabled, QComboBox:disabled { color: #8089a0; }
    QPlainTextEdit#StudioInstructions {
        color: #f5f4fe; background: #141621; border: 1px solid #393d53;
        border-radius: 8px; padding: 7px 10px;
        selection-background-color: #49396f; selection-color: #ffffff;
    }
    QPlainTextEdit#StudioInstructions:focus { border-color: #ad9afa; }
    QComboBox::drop-down { border: none; width: 24px; }
    QComboBox::down-arrow { image: none; }
    QComboBox QLineEdit { border: none; padding: 0; }
    QComboBox QAbstractItemView {
        background: #1f2231; color: #f0edff; border: 1px solid #42475e;
        selection-background-color: #3c325e; selection-color: #fff;
    }
    QPushButton {
        color: #eeebf8; background: #25283a; border: 1px solid #3b4057;
        border-radius: 8px; padding: 8px 12px; min-height: 18px;
    }
    QPushButton:hover { background: #303449; border-color: #575c77; }
    QPushButton:focus { border-color: #b3a1ff; }
    QPushButton:pressed { background: #202231; }
    QPushButton:disabled { color: #7f879e; }
    QPushButton#StudioNav {
        text-align: left; padding: 9px 12px; border: 1px solid transparent;
        color: #b9c0d5; background: transparent;
    }
    QPushButton#StudioNav:hover { background: #252738; color: #f3efff; }
    QPushButton#StudioNav:checked {
        background: #332b4a; color: #ede3ff; border-color: #4e4168; font-weight: 650;
    }
    QPushButton#StudioNav:focus { border-color: #baa9ff; }
    QPushButton#StudioSave { background: #bcabff; border-color: #c5b4ff; color: #211a34; font-weight: 700; }
    QPushButton#StudioSave:hover { background: #d2c5ff; }
    QPushButton#StudioSave:focus { border-color: #f2eaff; }
    QPushButton#StudioSwatch { text-align: left; padding: 7px 9px; background: #171925; }
    QPushButton#StudioSwatch:hover { background: #26293b; }
    QPushButton#StudioSwatch[colorSelected="true"] { background: #302840; border: 1px solid #ae9bf2; }
    QLabel#SavedKey { color: #a9e5c9; background: #263a36; border-radius: 6px; padding: 4px 8px; }
    QLabel#EmptyKey { color: #b1b8ce; background: #272a3d; border-radius: 6px; padding: 4px 8px; }
    QCheckBox { color: #e4e7f5; spacing: 9px; }
    QCheckBox:disabled { color: #7c859e; }
    QSlider::groove:horizontal { height: 5px; background: #393e55; border-radius: 2px; }
    QSlider::sub-page:horizontal { background: #ad9af1; border-radius: 2px; }
    QSlider::handle:horizontal { width: 13px; margin: -4px 0; background: #ddceff; border-radius: 6px; }
    QSlider::handle:horizontal:focus { background: #fff0ff; }
    QFrame#StudioPreviewInner { background: #1b1829; border-radius: 15px; }
    QLabel#StudioPreviewName { font-size: 15px; font-weight: 700; color: #f8f5ff; }
    QLabel#StudioPreviewPrompt {
        color: #b3aec7; background: #100f19; border: 1px solid #3c3850;
        border-radius: 9px; padding: 7px 10px;
    }
"""


class SettingsComboBox(QComboBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#bbc0d7" if self.isEnabled() else "#777f97"), 1.5))
        x, y = self.width() - 15, self.height() / 2
        painter.drawLine(QPointF(x - 4, y - 2), QPointF(x, y + 2))
        painter.drawLine(QPointF(x, y + 2), QPointF(x + 4, y - 2))
        painter.end()


class ColorWheelWidget(QWidget):
    """Interactive HSV colour wheel rendered with QConicalGradient + QRadialGradient."""

    color_changed = pyqtSignal(str)

    def __init__(self, initial_hex: str = "#a78bfa", parent=None):
        super().__init__(parent)
        self.setFixedSize(156, 156)
        self._hue = 260.0
        self._sat = 0.55
        self._val = 0.98
        self._dragging = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.set_color(initial_hex)

    def set_color(self, hex_color: str) -> None:
        c = QColor(hex_color)
        if not c.isValid():
            return
        h, s, v, _ = c.getHsvF()
        self._hue = max(0.0, h * 360.0) if h >= 0 else 0.0
        self._sat = max(0.0, min(1.0, s))
        self._val = max(0.05, min(1.0, v))
        self.update()

    def set_brightness(self, val_0_100: int) -> None:
        self._val = max(0.05, min(1.0, val_0_100 / 100.0))
        self.update()
        self.color_changed.emit(self.current_hex())

    def current_hex(self) -> str:
        c = QColor.fromHsvF(self._hue / 360.0, self._sat, self._val)
        return c.name()

    def brightness_percent(self) -> int:
        return round(self._val * 100)

    def _pick_at(self, pos: QPointF) -> None:
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        radius = min(cx, cy) - 6.0
        dx = pos.x() - cx
        dy = pos.y() - cy
        angle = (-math.degrees(math.atan2(dy, dx))) % 360.0
        sat = min(1.0, math.hypot(dx, dy) / radius)
        self._hue = angle
        self._sat = sat
        self.update()
        self.color_changed.emit(self.current_hex())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._pick_at(event.position())
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._pick_at(event.position())
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        radius = min(cx, cy) - 6.0
        rect = QRectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0)

        hue_grad = QConicalGradient(QPointF(cx, cy), 0.0)
        for step in range(7):
            frac = step / 6.0
            hue_grad.setColorAt(frac, QColor.fromHsvF(frac if frac < 1.0 else 0.0, 1.0, self._val))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(hue_grad)
        painter.drawEllipse(rect)

        sat_grad = QRadialGradient(QPointF(cx, cy), radius)
        center_white = QColor.fromHsvF(0.0, 0.0, self._val)
        edge_clear = QColor(center_white)
        edge_clear.setAlpha(0)
        sat_grad.setColorAt(0.0, center_white)
        sat_grad.setColorAt(1.0, edge_clear)
        painter.setBrush(sat_grad)
        painter.drawEllipse(rect)

        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        rad = math.radians(self._hue)
        px = cx + math.cos(rad) * self._sat * radius
        py = cy - math.sin(rad) * self._sat * radius
        painter.setPen(QPen(QColor("#ffffff"), 2.0))
        painter.setBrush(QColor(self.current_hex()))
        painter.drawEllipse(QPointF(px, py), 6.0, 6.0)
        painter.end()


class SetupDialog(QDialog):
    @staticmethod
    def _credential_row(saved_key: str, help_button: QPushButton | None = None) -> tuple[QLineEdit, QHBoxLayout]:
        is_saved = bool(saved_key)
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("Enter replacement" if is_saved else "Enter key")

        status = QLabel("Saved" if is_saved else "Not saved")
        status.setObjectName("SavedKey" if is_saved else "EmptyKey")
        status.setToolTip("This device has a saved key." if is_saved else "No key is saved on this device.")

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(field, 1)
        row.addWidget(status)
        if help_button is not None:
            row.addWidget(help_button)
        return field, row

    def _backup_credential_row(self, saved_key: str) -> tuple[QLineEdit, QCheckBox, QHBoxLayout]:
        field, row = self._credential_row(saved_key)
        remove = QCheckBox("Remove")
        remove.setEnabled(bool(saved_key))
        remove.toggled.connect(field.setDisabled)
        row.addWidget(remove)
        return field, remove, row

    def __init__(self, parent=None, *, show_desklet=None, voice=None):
        super().__init__(parent)
        self.setWindowTitle("liNi Settings" if IS_WINDOWS else "Set up liNi")
        if IS_WINDOWS:
            self.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowSystemMenuHint
                | Qt.WindowType.WindowMinMaxButtonsHint
                | Qt.WindowType.WindowCloseButtonHint
            )
        self.setWindowIcon(zylo_app_icon())
        self.setObjectName("ZyloSettings")
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(704, available.width() - 32), min(485, available.height() - 48))
        self.resize(min(1000, available.width() - 32), min(730, available.height() - 48))
        self.setStyleSheet(SETTINGS_STYLE)
        current = load_settings_config()
        saved_env = read_env_keys()
        self._catalog_draft = object()
        self._key_checks = {}
        self._flush_key_checks = []
        self._verified_groq = ""
        accent_cfg = get_accent_config(current)

        self.name = QLineEdit(current.get("name", ""))
        self.name.setPlaceholderText("What should liNi call you?")

        self.provider_combo = SettingsComboBox()
        self.lmstudio_enabled = QCheckBox("Show LM Studio in the provider switcher")
        self.lmstudio_enabled.setChecked(current["lmstudioEnabled"])
        self.freellmapi_enabled = QCheckBox("Show FreeLLMAPI in the provider switcher")
        self.freellmapi_enabled.setChecked(current["freeLlmApiEnabled"])
        self._refresh_provider_choices(current.get("defaultProvider") or read_provider(current))
        self.lmstudio_enabled.toggled.connect(lambda: self._refresh_provider_choices())
        self.freellmapi_enabled.toggled.connect(lambda: self._refresh_provider_choices())

        # LM Studio configuration fields
        self.lmstudio_url = QLineEdit(
            current.get("lmstudioBaseUrl") or saved_env.get("LMSTUDIO_BASE_URL") or DEFAULT_LMSTUDIO_BASE_URL
        )
        self.lmstudio_url.setPlaceholderText(DEFAULT_LMSTUDIO_BASE_URL)

        self.lmstudio_model = SettingsComboBox()
        self.lmstudio_model.setEditable(True)
        saved_model = current.get("lmstudioModel") or saved_env.get("LMSTUDIO_MODEL") or ""
        if saved_model:
            self.lmstudio_model.addItem(saved_model)
        self.lmstudio_model.addItem("")
        self.lmstudio_model.setCurrentText(saved_model)
        if self.lmstudio_model.lineEdit():
            self.lmstudio_model.lineEdit().setPlaceholderText("Auto-detect loaded model, or enter model ID")

        # Solid & Dynamic Gradient Accent controls
        self.accent_mode_combo = SettingsComboBox()
        self.accent_mode_combo.addItem("Solid accent", "solid")
        self.accent_mode_combo.addItem("Dynamic gradient", "gradient")
        mode_idx = self.accent_mode_combo.findData(accent_cfg["mode"])
        if mode_idx >= 0:
            self.accent_mode_combo.setCurrentIndex(mode_idx)
        self.accent_mode_combo.currentIndexChanged.connect(self._on_accent_mode_changed)

        self.gradient_preset_combo = SettingsComboBox()
        for preset_name in GRADIENT_PRESETS:
            self.gradient_preset_combo.addItem(preset_name, preset_name)
        self.gradient_preset_combo.addItem("Custom Gradient", "custom")
        self.gradient_preset_combo.currentIndexChanged.connect(self._on_gradient_preset_changed)

        self.accent = accent_cfg["accent"]
        self.gradient_start = accent_cfg["gradientStart"]
        self.gradient_mid = accent_cfg["gradientMid"]
        self.gradient_end = accent_cfg["gradientEnd"]
        self._active_wheel_target = (
            "start" if accent_cfg["mode"] == "gradient" else "solid"
        )

        self.accent_button = QPushButton()
        self.accent_button.clicked.connect(lambda: self._select_wheel_target("solid"))

        self.grad_start_btn = QPushButton()
        self.grad_start_btn.clicked.connect(lambda: self._select_wheel_target("start"))
        self.grad_mid_btn = QPushButton()
        self.grad_mid_btn.clicked.connect(lambda: self._select_wheel_target("mid"))
        self.grad_end_btn = QPushButton()
        self.grad_end_btn.clicked.connect(lambda: self._select_wheel_target("end"))

        self.color_wheel = ColorWheelWidget(
            self.gradient_start if accent_cfg["mode"] == "gradient" else self.accent
        )
        self.color_wheel.color_changed.connect(self._on_wheel_color_changed)

        self.wheel_brightness = QSlider(Qt.Orientation.Horizontal)
        self.wheel_brightness.setRange(5, 100)
        self.wheel_brightness.setValue(self.color_wheel.brightness_percent())
        self.wheel_brightness.valueChanged.connect(self.color_wheel.set_brightness)

        self.wheel_hex_input = QLineEdit()
        self.wheel_hex_input.setMaxLength(7)
        self.wheel_hex_input.setPlaceholderText("#a78bfa")
        self.wheel_hex_input.textEdited.connect(self._on_hex_edited)

        self.wheel_target_label = QLabel()
        self.wheel_system_btn = QPushButton("OS Picker…")
        self.wheel_system_btn.clicked.connect(self._open_system_color_dialog)

        self.startup_checkbox = QCheckBox("Launch liNi automatically when I sign in to Windows 11")
        if IS_WINDOWS:
            self.startup_checkbox.setChecked(
                current.get("launchOnStartup", is_windows_startup_enabled())
            )
        else:
            self.startup_checkbox.hide()

        profile = self._studio_form()
        profile.addRow("Your name", self.name)
        profile.addRow("Default provider", self.provider_combo)
        self.memory_turns = QSlider(Qt.Orientation.Horizontal)
        self.memory_turns.setAccessibleName("Conversation history")
        self.memory_turns.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.memory_turns.setRange(MIN_MEMORY_TURNS, MAX_MEMORY_TURNS)
        self.memory_turns.setPageStep(5)
        self.memory_turns.setValue(get_memory_turns(current))
        turns_label = QLabel(f"{self.memory_turns.value()} turns")
        turns_label.setObjectName("StudioMemoryTurns")
        turns_label.setMinimumWidth(60)
        turns_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.memory_turns.valueChanged.connect(lambda turns: turns_label.setText(f"{turns} turns"))
        memory_row = QHBoxLayout()
        memory_row.setSpacing(12)
        memory_row.addWidget(self.memory_turns, 1)
        memory_row.addWidget(turns_label)
        memory = self._studio_form()
        memory.addRow("Conversation history", memory_row)
        general_cards = [
            self._studio_card("Profile", profile),
            self._studio_card("Memory", memory, "Keeps the latest turns on this device. A turn is your message and liNi’s reply."),
        ]

        nvidia = self._studio_form()
        self.key, key_row = self._credential_row(saved_env["NVIDIA_API_KEY"], self._website_button("Get key", API_KEY_URL))
        self.key.setAccessibleName("NVIDIA API key")
        nvidia.addRow("Primary key", key_row)
        self.nvidia_backup_key, self.remove_nvidia_backup, nvidia_backup_row = self._backup_credential_row(
            saved_env["NVIDIA_BACKUP_API_KEY"]
        )
        self.nvidia_backup_key.setAccessibleName("NVIDIA backup key")
        nvidia.addRow("Backup key", nvidia_backup_row)

        groq = self._studio_form()
        self.groq_key, groq_row = self._credential_row(
            saved_env["GROQ_API_KEY"], self._website_button("Get key", GROQ_API_KEY_URL)
        )
        self.groq_key.setAccessibleName("Groq API key")
        groq.addRow("Primary key", groq_row)
        self.groq_backup_key, self.remove_groq_backup, groq_backup_row = self._backup_credential_row(
            saved_env["GROQ_BACKUP_API_KEY"]
        )
        self.groq_backup_key.setAccessibleName("Groq backup key")
        groq.addRow("Backup key", groq_backup_row)
        if IS_WINDOWS:
            groq.addRow(self._muted(
                "Once the Groq key is verified, liNi can send installed app names to Groq to build "
                "the list in the background. Launch paths stay on this PC. Control this in System."
            ))

        local = self._studio_form()
        if IS_WINDOWS:
            local.addRow(self.lmstudio_enabled)
        lms_url_row = QHBoxLayout()
        lms_url_row.setSpacing(8)
        lms_url_row.addWidget(self.lmstudio_url, 1)
        lms_url_row.addWidget(self._website_button("Docs", LMSTUDIO_DOCS_URL))
        self.lmstudio_url.setAccessibleName("LM Studio endpoint URL")
        local.addRow("Endpoint", lms_url_row)
        lms_model_row = QHBoxLayout()
        lms_model_row.setSpacing(8)
        lms_model_row.addWidget(self.lmstudio_model, 1)
        detect_btn = QPushButton("Detect models")
        detect_btn.clicked.connect(self._detect_lmstudio_models)
        lms_model_row.addWidget(detect_btn)
        self.lmstudio_model.setAccessibleName("LM Studio model")
        local.addRow("Model", lms_model_row)
        self.lmstudio_key, lms_key_row = self._credential_row(saved_env["LMSTUDIO_API_KEY"])
        self.lmstudio_key.setAccessibleName("LM Studio token")
        local.addRow("Optional token", lms_key_row)

        search = self._studio_form()
        self.tavily_key, tavily_row = self._credential_row(
            saved_env["TAVILY_API_KEY"], self._website_button("Get key", TAVILY_API_KEY_URL)
        )
        self.tavily_key.setAccessibleName("Tavily search key")
        search.addRow("Optional key", tavily_row)
        provider_cards = [
            self._studio_card("NVIDIA NIM", nvidia),
            self._studio_card("Groq", groq),
            self._studio_card("LM Studio", local),
            self._studio_card("Tavily web search", search),
        ]
        if IS_WINDOWS:
            free = self._studio_form()
            free.addRow(self.freellmapi_enabled)
            self.freellmapi_key, free_key_row = self._credential_row(saved_env["FREELLMAPI_API_KEY"])
            self.freellmapi_key.setAccessibleName("FreeLLMAPI unified key")
            free.addRow("Unified API key", free_key_row)
            self.freellmapi_model = SettingsComboBox()
            for model, label in FREELLMAPI_ROUTES.items():
                self.freellmapi_model.addItem(label, model)
            self.freellmapi_model.setCurrentIndex(self.freellmapi_model.findData(current["freeLlmApiModel"]))
            free.addRow("Routing", self.freellmapi_model)
            free.addRow("Local endpoint", self._muted(FREELLMAPI_BASE_URL))
            provider_cards.insert(-1, self._studio_card(
                "FreeLLMAPI", free, "Start FreeLLMAPI on this PC and paste its unified API key.",
            ))

        self._theme_form = self._studio_form()
        self._theme_form.addRow("Color style", self.accent_mode_combo)
        self._theme_form.addRow("Accent", self.accent_button)
        self._theme_form.addRow("Preset", self.gradient_preset_combo)
        self._gradient_stops = QHBoxLayout()
        self._gradient_stops.setSpacing(7)
        for swatch in (self.grad_start_btn, self.grad_mid_btn, self.grad_end_btn):
            self._gradient_stops.addWidget(swatch, 1)
        self._theme_form.addRow("Gradient stops", self._gradient_stops)
        wheel_box = QHBoxLayout()
        wheel_box.setSpacing(20)
        wheel_box.addWidget(self.color_wheel)
        wheel_controls = QVBoxLayout()
        wheel_controls.setSpacing(8)
        wheel_controls.addWidget(self.wheel_target_label)
        wheel_controls.addWidget(self._muted("Brightness"))
        wheel_controls.addWidget(self.wheel_brightness)
        hex_row = QHBoxLayout()
        self.wheel_hex_input.setAccessibleName("Hex color")
        hex_row.addWidget(self.wheel_hex_input, 1)
        self.wheel_system_btn.setText("System picker")
        hex_row.addWidget(self.wheel_system_btn)
        wheel_controls.addLayout(hex_row)
        wheel_box.addLayout(wheel_controls, 1)
        appearance = QVBoxLayout()
        appearance.setSpacing(14)
        appearance.addWidget(self._build_desklet_preview())
        appearance.addLayout(self._theme_form)
        appearance.addLayout(wheel_box)
        appearance_cards = [self._studio_card("Desklet theme", appearance)]
        if IS_WINDOWS:
            self.chat_bubble_color = current["chatBubbleColor"]
            self.chat_bubble_button = QPushButton()
            self.chat_bubble_button.setAccessibleName("Choose user message color")
            self.chat_bubble_button.clicked.connect(self._choose_chat_bubble_color)
            self._update_chat_bubble_button()
            bubble_form = self._studio_form()
            bubble_form.addRow("User message color", self.chat_bubble_button)
            appearance_cards.append(self._studio_card("Chat bubbles", bubble_form))

        system = QVBoxLayout()
        system.setSpacing(10)
        if IS_WINDOWS:
            system.addWidget(self.startup_checkbox)
            self.admin_tools_checkbox = QCheckBox("Allow requests for Windows administrator tools")
            self.admin_tools_checkbox.setChecked(current.get("allowAdminToolRequests") is True)
            system.addWidget(self.admin_tools_checkbox)
            system.addWidget(self._muted(
                "liNi can ask to open tools such as Windows Features, Services, and Event Viewer. "
                "You approve each launch, then make changes in the Windows tool."
            ))

        self.skills_manager_btn = QPushButton("Manage skills")
        self.skills_manager_btn.clicked.connect(self._open_skill_manager)
        skills = QHBoxLayout()
        skills.addWidget(self._muted("Review trusted scripts and their execution history."), 1)
        skills.addWidget(self.skills_manager_btn)
        system_cards = [self._studio_card("PowerShell skills" if IS_WINDOWS else "Skills", skills)]
        if IS_WINDOWS:
            system_cards.insert(0, self._studio_card("Startup and permissions", system))
            search_depth = self._studio_form()
            self.file_search_depths = {}
            for folder in DEFAULT_FILE_SEARCH_LEVELS:
                level = QSpinBox()
                level.setRange(0, MAX_FILE_SEARCH_LEVEL_INPUT)
                level.setSpecialValueText("Unlimited")
                level.setValue(current["fileSearchLevels"][folder])
                level.setMaximumWidth(140)
                level.setAccessibleName(f"{folder.title()} file search level")
                self.file_search_depths[folder] = level
                search_depth.addRow(folder.title(), level)
            system_cards.append(self._studio_card(
                "File search depth", search_depth, "Set the last folder level to search in each location.",
                help_text=(
                    "The drive root is level 1. Each folder adds one level.\n"
                    "Files inside the last allowed folder are included.\n"
                    "Enter 0 to search all subfolders in a location."
                ),
            ))
            catalog_box = QVBoxLayout()
            self.filter_apps_with_groq = QCheckBox("Use Groq to filter the app list")
            self.filter_apps_with_groq.setChecked(current["filterAppsWithGroq"])
            self._catalog_status = self._muted(self._app_catalog_summary())
            self.rebuild_app_list_btn = QPushButton("Rebuild app list")
            self.rebuild_app_list_btn.clicked.connect(self._rebuild_app_list)
            from step_desklet.win11.setup_jobs import AppListJobs

            self._catalog_jobs = AppListJobs.for_app()
            self._catalog_jobs.changed.connect(self._refresh_app_list_status)
            self.filter_apps_with_groq.toggled.connect(self._app_filter_changed)
            self._refresh_app_list_status()
            for field, row, provider in (
                (self.key, key_row, "nim"),
                (self.nvidia_backup_key, nvidia_backup_row, "nim"),
                (self.groq_key, groq_row, "groq"),
                (self.groq_backup_key, groq_backup_row, "groq"),
                (self.tavily_key, tavily_row, "tavily"),
                (self.lmstudio_key, lms_key_row, "lmstudio"),
                (self.freellmapi_key, free_key_row, "freellmapi"),
            ):
                self._watch_key(field, row.itemAt(1).widget(), provider)
            catalog_box.addWidget(self.filter_apps_with_groq)
            catalog_box.addWidget(self._catalog_status)
            catalog_box.addWidget(self.rebuild_app_list_btn, 0, Qt.AlignmentFlag.AlignLeft)
            system_cards.append(self._studio_card(
                "Installed app list", catalog_box,
                "Rebuild after installing or removing apps. Groq receives names only. "
                "Without a Groq key, liNi filters the list locally.",
            ))

        voice_sections = ()
        if IS_WINDOWS:
            self.custom_instructions = QPlainTextEdit(current["customInstructions"])
            self.custom_instructions.setObjectName("StudioInstructions")
            self.custom_instructions.setPlaceholderText("Preferred language, tone, or level of detail")
            self.custom_instructions.setTabChangesFocus(True)
            self.custom_instructions.document().setDocumentMargin(2)
            self.custom_instructions.setFixedHeight(3 * self.custom_instructions.fontMetrics().lineSpacing() + 18)
            instructions_palette = self.custom_instructions.palette()
            instructions_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#a5adc2"))
            self.custom_instructions.setPalette(instructions_palette)
            self.weather_location = QLineEdit(current["weatherLocation"])
            self.weather_location.setPlaceholderText("Latitude, longitude")
            self.weather_location.setMaxLength(64)
            preferences = self._studio_form()
            preferences.addRow("Custom instructions", self.custom_instructions)
            preferences.addRow("Weather coordinates", self.weather_location)
            general_cards.append(self._studio_card(
                "Assistant preferences", preferences,
                "Your provider receives your instructions and the PC's local time. "
                "For weather questions, optional coordinates let liNi look up current conditions.",
            ))
            voice_sections = (("Voice", "Hold a shortcut to speak. Release it to send.", self._voice_cards(current, voice)),)
        self._build_studio(
            (
                ("General", "Set your profile, preferred model provider, and conversation memory.", general_cards),
                ("Providers", "Manage model connections. Leave any saved key field blank to keep it.", provider_cards),
                ("Appearance", "Tune your desklet and chat colors.", appearance_cards),
                ("System", "Manage startup, Windows tools, skills, and file search.", system_cards),
                *voice_sections,
            ),
            show_desklet,
        )
        self._sync_gradient_preset_selection()
        self._on_accent_mode_changed()
        self.name.setFocus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if IS_WINDOWS:
            use_dark_title_bar(int(self.winId()))

    def _voice_cards(self, current, voice):
        from PyQt6.QtGui import QKeySequence
        from step_desklet.win11.voice import VoiceController
        from step_desklet.win11.voice_models import ASSETS, STT, TTS, VoiceDownloads, models_root

        settings = current["voice"]
        self._voice_controller = voice
        self.voice_enabled = QCheckBox("Enable Voice Mode")
        self.voice_enabled.setChecked(settings["enabled"])
        self.voice_global = QCheckBox("Enable the global push-to-talk shortcut")
        self.voice_global.setChecked(settings["globalShortcut"])
        self.voice_shortcut = QKeySequenceEdit(QKeySequence.fromString(settings["shortcut"]))
        self.voice_shortcut.setMaximumSequenceLength(1)
        self.voice_shortcut.setToolTip("Win + Space is tried first. Windows may reserve it for switching keyboard layouts.")
        self.voice_global.toggled.connect(self.voice_shortcut.setEnabled)
        self.voice_shortcut.setEnabled(self.voice_global.isChecked())
        self.voice_review = QCheckBox("Review the transcript before sending")
        self.voice_review.setChecked(settings["review"])
        self.voice_spoken = QCheckBox("Read replies aloud for voice messages")
        self.voice_spoken.setChecked(settings["spokenReplies"])
        self.voice_tts = SettingsComboBox()
        self.voice_tts.addItem("Windows speech", "windows")
        self.voice_tts.addItem("Pocket TTS (experimental)", "pocket")
        self.voice_tts.setCurrentIndex(self.voice_tts.findData(settings["ttsEngine"]))
        self.voice_tts.setToolTip("Windows speech plays through the default output directly. Save changes to enable Pocket TTS.")
        self.voice_mic = SettingsComboBox()
        self.voice_speaker = SettingsComboBox()
        for combo, devices, key in (
            (self.voice_mic, VoiceController.inputs(), "microphone"),
            (self.voice_speaker, VoiceController.outputs(), "speaker"),
        ):
            combo.addItem("Windows default", "")
            for device in devices:
                combo.addItem(device.description(), VoiceController.device_key(device))
            index = combo.findData(settings[key])
            if index < 0 and settings[key]:
                combo.addItem("Saved device (unavailable)", settings[key])
                index = combo.count() - 1
            combo.setCurrentIndex(max(0, index))
        info = self._muted(voice._hotkey_result if voice else "Enable Voice Mode and save to activate the shortcut.")
        meter = QProgressBar()
        meter.setTextVisible(False)
        test_mic = QPushButton("Test microphone")
        test_speaker = QPushButton("Test speaker")
        test_mic.setEnabled(bool(voice))
        test_speaker.setEnabled(bool(voice))
        if voice:
            voice.status.connect(info.setText)
            voice.test_level.connect(meter.setValue)
            test_mic.clicked.connect(lambda: voice.test_microphone(self.voice_mic.currentData()))
            test_speaker.clicked.connect(
                lambda: voice.test_speaker(self.voice_speaker.currentData(), self.voice_tts.currentData()),
            )
            self.finished.connect(voice.stop_microphone_test)
        input_form = self._studio_form()
        input_form.addRow(self.voice_enabled)
        input_form.addRow(self.voice_global)
        input_form.addRow("Shortcut", self.voice_shortcut)
        input_form.addRow(info)
        input_form.addRow("Microphone", self.voice_mic)
        input_form.addRow(test_mic, meter)
        input_form.addRow(self.voice_review)
        output_form = self._studio_form()
        output_form.addRow(self.voice_spoken)
        output_form.addRow("Speech engine", self.voice_tts)
        output_form.addRow("Output device", self.voice_speaker)
        output_form.addRow(test_speaker)

        downloads = VoiceDownloads.for_app()
        model_form = self._studio_form()
        bars = {}
        for folder, filename, *_ in ASSETS:
            label = {STT: "Whisper Small Q8 · 270 MB", TTS: "Pocket TTS · 110 MB"}.get(filename, "Speech output runtime")
            bar = QProgressBar()
            bar.setFormat("Saved" if (models_root() / folder / filename).is_file() else "Not downloaded")
            bar.setValue(100 if (models_root() / folder / filename).is_file() else 0)
            bars[filename] = bar
            model_form.addRow(label, bar)
        download = QPushButton("Download voice models")
        cancel = QPushButton("Cancel download")
        remove = QPushButton("Remove models")
        download.setEnabled(not downloads.running)
        cancel.setEnabled(downloads.running)
        model_status = self._muted("Downloading…" if downloads.running else "")

        def start():
            download.setEnabled(False)
            cancel.setEnabled(True)
            model_status.setText("Downloading missing voice files…")
            downloads.start(include_pocket=self.voice_tts.currentData() == "pocket")

        def remove_models():
            if downloads.running or (voice and voice.settings.get("enabled")):
                model_status.setText("Stop the download or turn Voice Mode off and save before removing models.")
                return
            if QMessageBox.question(self, "Remove voice models", "Remove both downloaded speech models from this PC?") != QMessageBox.StandardButton.Yes:
                return
            try:
                for subfolder, filename in (("stt", STT), ("tts", TTS)):
                    (models_root() / subfolder / filename).unlink(missing_ok=True)
                    bars[filename].setValue(0)
                    bars[filename].setFormat("Not downloaded")
            except OSError:
                model_status.setText("A voice model is still in use. Close liNi before removing it.")
                return
            model_status.setText("Voice models removed.")

        def progress(name, percent):
            bars[name].setFormat("Verified" if percent == 100 else "%p%")
            bars[name].setValue(percent)

        def finished(ok, message):
            model_status.setText(message)
            download.setText("Verify voice models" if ok else "Retry download")
            download.setEnabled(True)
            cancel.setEnabled(False)

        download.clicked.connect(start)
        cancel.clicked.connect(downloads.cancelled.set)
        remove.clicked.connect(remove_models)
        downloads.progress.connect(progress)
        downloads.finished.connect(finished)
        self.destroyed.connect(lambda: self._disconnect_voice_downloads(downloads, progress, finished))
        actions = QHBoxLayout()
        for button in (download, cancel, remove):
            actions.addWidget(button)
        model_form.addRow(actions)
        model_form.addRow(model_status)
        return [
            self._studio_card("Push to talk", input_form),
            self._studio_card("Spoken replies", output_form),
            self._studio_card("Voice models", model_form, "One download sets up local transcription and English speech."),
        ]

    @staticmethod
    def _disconnect_voice_downloads(downloads, *slots):
        for signal, slot in zip((downloads.progress, downloads.finished), slots):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _refresh_provider_choices(self, preferred=None) -> None:
        current = preferred or self.provider_combo.currentData()
        providers = enabled_providers({
            "lmstudioEnabled": self.lmstudio_enabled.isChecked(),
            "freeLlmApiEnabled": self.freellmapi_enabled.isChecked(),
        })
        self.provider_combo.clear()
        for provider in providers:
            self.provider_combo.addItem(PROVIDER_LABELS[provider], provider)
        index = self.provider_combo.findData(current)
        self.provider_combo.setCurrentIndex(max(0, index))

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("StudioMuted")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _website_button(text: str, address: str) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(address)))
        return button

    @staticmethod
    def _studio_form() -> QFormLayout:
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        return form

    def _studio_card(self, title: str, content, description: str = "", *, help_text: str = "") -> QFrame:
        card = QFrame()
        card.setObjectName("StudioCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(19, 16, 19, 17)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("StudioCardTitle")
        if help_text:
            help_icon = QLabel("ⓘ")
            help_icon.setObjectName("StudioInfoIcon")
            help_icon.setAccessibleName(f"About {title.lower()}")
            help_icon.setAccessibleDescription(help_text)
            help_icon.setToolTip(help_text)
            header = QHBoxLayout()
            header.addWidget(heading)
            header.addWidget(help_icon)
            header.addStretch()
            layout.addLayout(header)
        else:
            layout.addWidget(heading)
        if description:
            layout.addWidget(self._muted(description))
        layout.addLayout(content)
        return card

    def _build_desklet_preview(self) -> QFrame:
        self.grad_preview = QFrame()
        self.grad_preview.setObjectName("StudioPreviewRim")
        self.grad_preview.setAccessibleName("Desklet preview")
        rim = QVBoxLayout(self.grad_preview)
        rim.setContentsMargins(2, 2, 2, 2)
        surface = QFrame()
        surface.setObjectName("StudioPreviewInner")
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(14, 10, 14, 10)
        surface_layout.setSpacing(6)
        preview_name = QLabel()
        preview_name.setObjectName("StudioPreviewName")
        preview_name.setTextFormat(Qt.TextFormat.PlainText)

        def update_name(name):
            preview_name.setText(name.strip()[:32].upper() or "liNi")

        update_name(self.name.text())
        self.name.textChanged.connect(update_name)
        surface_layout.addWidget(preview_name)
        prompt = QLabel("Ask liNi")
        prompt.setObjectName("StudioPreviewPrompt")
        surface_layout.addWidget(prompt)
        rim.addWidget(surface)
        return self.grad_preview

    def _build_studio(self, sections, show_desklet) -> None:
        sidebar = QFrame()
        sidebar.setObjectName("StudioSidebar")
        sidebar.setFixedWidth(178)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(16, 22, 16, 18)
        navigation.setSpacing(7)
        logo = QLabel()
        logo.setFixedSize(39, 39)
        logo.setPixmap(self.windowIcon().pixmap(QSize(39, 39), self.devicePixelRatioF()))
        brand_name = QLabel("liNi")
        brand_name.setObjectName("StudioBrand")
        brand = QHBoxLayout()
        brand.setSpacing(8)
        brand.addWidget(logo)
        brand.addWidget(brand_name, 1)
        navigation.addLayout(brand)
        navigation.addSpacing(20)

        self._settings_pages = QStackedWidget()
        self._navigation_buttons = QButtonGroup(self)
        self._section_indices = {}
        for index, (name, description, cards) in enumerate(sections):
            button = QPushButton(name)
            button.setObjectName("StudioNav")
            button.setCheckable(True)
            self._navigation_buttons.addButton(button, index)
            navigation.addWidget(button)
            self._section_indices[name] = index

            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(32, 23, 22, 0)
            page_layout.setSpacing(7)
            title = QLabel(name)
            title.setObjectName("StudioTitle")
            page_layout.addWidget(title)
            page_layout.addWidget(self._muted(description))
            page_layout.addSpacing(11)

            content = QWidget()
            content.setObjectName("StudioScrollContent")
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 1, 10, 18)
            content_layout.setSpacing(12)
            for card in cards:
                content_layout.addWidget(card)
            content_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            page_layout.addWidget(scroll, 1)
            self._settings_pages.addWidget(page)

        navigation.addStretch(1)
        self._navigation_buttons.idClicked.connect(self._settings_pages.setCurrentIndex)
        self._select_section("General")

        footer = QFrame()
        footer.setObjectName("StudioFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(32, 13, 32, 13)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setObjectName("StudioSave")
        save.setText("Save changes")
        save.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if show_desklet is not None:
            desklet_button = QPushButton("Show Desklet")
            desklet_button.setObjectName("StudioShowDesklet")
            desklet_button.clicked.connect(lambda: show_desklet(self._theme_values()))
            footer_layout.addWidget(desklet_button)
            footer_layout.addStretch(1)
        footer_layout.addWidget(buttons)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._settings_pages, 1)
        main.addWidget(footer)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addLayout(main, 1)

    def _select_section(self, name: str, field: QWidget | None = None) -> None:
        index = self._section_indices[name]
        self._navigation_buttons.button(index).setChecked(True)
        self._settings_pages.setCurrentIndex(index)
        if field is not None:
            field.setFocus()
            scroll = self._settings_pages.currentWidget().findChild(QScrollArea)
            scroll.ensureWidgetVisible(field)


    def accept(self):
        if not save_setup(self):
            return
        if IS_WINDOWS:
            self._prepare_app_list(saved=True)
            for flush in self._flush_key_checks:
                flush()
            self._key_checks.clear()
        super().accept()

    def reject(self):
        if IS_WINDOWS:
            self._catalog_jobs.cancel_draft(self._catalog_draft)
            self._cancel_key_checks()
        super().reject()

    @staticmethod
    def _app_catalog_summary() -> str:
        catalog = load_app_catalog()
        if catalog is None:
            return "No saved app list yet."
        source = "Groq" if catalog.source == "groq" else "Local filter"
        return f"{len(catalog_names(catalog.apps))} saved apps · {source} · {catalog.updated}"

    def _app_list_keys(self) -> tuple[str, str]:
        if not self.filter_apps_with_groq.isChecked():
            return "", ""
        keys = read_env_keys()
        primary = self.groq_key.text().strip() or keys["GROQ_API_KEY"]
        backup = "" if self.remove_groq_backup.isChecked() else (
            self.groq_backup_key.text().strip() or keys["GROQ_BACKUP_API_KEY"]
        )
        return primary, backup if primary else ""

    def _rebuild_app_list(self):
        primary, backup = self._app_list_keys()
        self._catalog_jobs.start(
            primary, backup, check_key=bool(primary and primary != self._verified_groq),
            draft=self._catalog_draft,
        )

    def _refresh_app_list_status(self):
        self.rebuild_app_list_btn.setEnabled(self._catalog_jobs.request is None)
        self._catalog_status.setText(self._catalog_jobs.status or self._app_catalog_summary())

    def _app_filter_changed(self, enabled: bool) -> None:
        if not enabled:
            self._catalog_jobs.cancel_draft(self._catalog_draft)
        elif self._verified_groq:
            self._prepare_app_list()

    def _prepare_app_list(self, *, saved: bool = False) -> None:
        primary, backup = self._app_list_keys()
        jobs = self._catalog_jobs
        same_job = jobs.credentials == (primary, backup)
        if saved and jobs.draft is self._catalog_draft and same_job:
            jobs.draft = None
            return
        if saved and jobs.request is not None and not same_job:
            jobs.cancel()
        if primary and not saved and primary != self._verified_groq:
            return
        catalog = load_app_catalog()
        if catalog is None or (primary and catalog.source != "groq"):
            jobs.start(
                primary, backup, check_key=bool(primary and primary != self._verified_groq),
                draft=None if saved else self._catalog_draft,
            )

    def _watch_key(self, field: QLineEdit, label: QLabel, provider: str) -> None:
        from step_desklet.win11.request_process import DeskletRequest

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(450)
        initial_label = label.text()

        def changed():
            timer.stop()
            if previous := self._key_checks.pop(field, None):
                previous.cancel()
            if field is self.groq_key:
                self._verified_groq = ""
                self._catalog_jobs.cancel_draft(self._catalog_draft)
            if field.text().strip() and field.isEnabled():
                label.setText("Checking...")
                timer.start()
            else:
                label.setText(initial_label)

        def check():
            key = field.text().strip()
            if not key or not field.isEnabled():
                return
            app = QApplication.instance()
            request = DeskletRequest(app)
            app.aboutToQuit.connect(request.cancel)
            self._key_checks[field] = request

            def finished(event, message):
                if self._key_checks.get(field) is not request:
                    return
                self._key_checks.pop(field)
                label.setText("Verified" if event == "response" else message)
                if event == "response" and field in (self.key, self.groq_key, self.lmstudio_key):
                    if field is self.groq_key:
                        self._verified_groq = key
                    self._prepare_app_list()

            request.outcome.connect(finished)
            request.start_request({
                "operation": "check_provider_key", "provider": provider, "key": key,
                "base_url": self.lmstudio_url.text().strip() if provider == "lmstudio" else "",
                "model": self.freellmapi_model.currentData() if provider == "freellmapi" else "",
            })

        def flush():
            catalog_keys = self._catalog_jobs.credentials
            if field is self.groq_key and catalog_keys and catalog_keys[0] == field.text().strip():
                if request := self._key_checks.pop(field, None):
                    request.cancel()
            elif timer.isActive():
                check()
            timer.stop()

        self._flush_key_checks.append(flush)
        timer.timeout.connect(check)
        field.textChanged.connect(changed)
        self.finished.connect(timer.stop)
        if provider == "lmstudio":
            self.lmstudio_url.textChanged.connect(changed)
        if provider == "freellmapi":
            self.freellmapi_model.currentIndexChanged.connect(changed)
        if field is self.groq_backup_key:
            self.remove_groq_backup.toggled.connect(changed)
        if field is self.nvidia_backup_key:
            self.remove_nvidia_backup.toggled.connect(changed)

    def _cancel_key_checks(self) -> None:
        for request in self._key_checks.values():
            request.cancel()
        self._key_checks.clear()

    def _open_skill_manager(self):
        dlg = SkillManagerDialog(parent=self)
        dlg.exec()

    def _sync_gradient_preset_selection(self):
        tuple_now = (self.gradient_start.lower(), self.gradient_mid.lower(), self.gradient_end.lower())
        for name, stops in GRADIENT_PRESETS.items():
            if tuple_now == (stops[0].lower(), stops[1].lower(), stops[2].lower()):
                idx = self.gradient_preset_combo.findData(name)
                if idx >= 0:
                    self.gradient_preset_combo.setCurrentIndex(idx)
                return
        idx = self.gradient_preset_combo.findData("custom")
        if idx >= 0:
            self.gradient_preset_combo.setCurrentIndex(idx)

    def _on_accent_mode_changed(self):
        is_grad = self.accent_mode_combo.currentData() == "gradient"
        self._theme_form.setRowVisible(self.accent_button, not is_grad)
        self._theme_form.setRowVisible(self.gradient_preset_combo, is_grad)
        self._theme_form.setRowVisible(self._gradient_stops, is_grad)
        self.accent_button.setEnabled(not is_grad)
        self.gradient_preset_combo.setEnabled(is_grad)
        self.grad_start_btn.setEnabled(is_grad)
        self.grad_mid_btn.setEnabled(is_grad)
        self.grad_end_btn.setEnabled(is_grad)
        if is_grad and self._active_wheel_target == "solid":
            self._active_wheel_target = "start"
        elif not is_grad:
            self._active_wheel_target = "solid"
        self._sync_wheel_to_target()
        self._show_accent()

    def _on_gradient_preset_changed(self):
        preset_key = self.gradient_preset_combo.currentData()
        if preset_key in GRADIENT_PRESETS:
            s, m, e = GRADIENT_PRESETS[preset_key]
            self.gradient_start = s
            self.gradient_mid = m
            self.gradient_end = e
            self._sync_wheel_to_target()
            self._show_accent()

    def _detect_lmstudio_models(self):
        base_url = self.lmstudio_url.text().strip() or DEFAULT_LMSTUDIO_BASE_URL
        api_key = self.lmstudio_key.text().strip() or read_env_keys().get("LMSTUDIO_API_KEY", "")
        try:
            models = list_lmstudio_models(base_url, api_key)
        except Exception as exc:
            QMessageBox.warning(self, "LM Studio detection failed", str(exc))
            return
        if not models:
            QMessageBox.information(
                self,
                "No loaded models",
                "Connected to LM Studio, but no model is currently loaded. Load a model in LM Studio and try again.",
            )
            return
        current_text = self.lmstudio_model.currentText().strip()
        self.lmstudio_model.clear()
        for model_id in models:
            self.lmstudio_model.addItem(model_id)
        if current_text and current_text in models:
            self.lmstudio_model.setCurrentText(current_text)
        else:
            self.lmstudio_model.setCurrentText(models[0])
        QMessageBox.information(
            self,
            "LM Studio connected",
            f"Detected {len(models)} model(s). Selected: {self.lmstudio_model.currentText()}",
        )

    def _target_color(self) -> str:
        return {
            "solid": self.accent,
            "start": self.gradient_start,
            "mid": self.gradient_mid,
            "end": self.gradient_end,
        }.get(self._active_wheel_target, self.accent)

    def _apply_color_to_target(self, hex_color: str) -> None:
        if self._active_wheel_target == "solid":
            self.accent = hex_color
        elif self._active_wheel_target == "start":
            self.gradient_start = hex_color
            self.accent = hex_color
        elif self._active_wheel_target == "mid":
            self.gradient_mid = hex_color
        else:
            self.gradient_end = hex_color
        self._sync_gradient_preset_selection()
        self._show_accent()

    def _select_wheel_target(self, target: str) -> None:
        self._active_wheel_target = target
        self._sync_wheel_to_target()
        self._show_accent()

    def _sync_wheel_to_target(self) -> None:
        names = {
            "solid": "Accent color",
            "start": "Start color",
            "mid": "Middle color",
            "end": "End color",
        }
        hex_val = self._target_color()
        self.wheel_target_label.setText(names.get(self._active_wheel_target, "Editing color"))
        self.color_wheel.blockSignals(True)
        self.color_wheel.set_color(hex_val)
        self.color_wheel.blockSignals(False)
        self.wheel_brightness.blockSignals(True)
        self.wheel_brightness.setValue(self.color_wheel.brightness_percent())
        self.wheel_brightness.blockSignals(False)
        self.wheel_hex_input.setText(hex_val)

    def _on_wheel_color_changed(self, hex_color: str) -> None:
        self.wheel_hex_input.setText(hex_color)
        self._apply_color_to_target(hex_color)

    def _on_hex_edited(self, text: str) -> None:
        if re.fullmatch(r"#[0-9a-fA-F]{6}", text.strip()):
            hex_val = text.strip().lower()
            self.color_wheel.blockSignals(True)
            self.color_wheel.set_color(hex_val)
            self.color_wheel.blockSignals(False)
            self.wheel_brightness.blockSignals(True)
            self.wheel_brightness.setValue(self.color_wheel.brightness_percent())
            self.wheel_brightness.blockSignals(False)
            self._apply_color_to_target(hex_val)

    def _open_system_color_dialog(self) -> None:
        current = QColor(self._target_color())
        color = QColorDialog.getColor(current, parent=self, title="Choose liNi color")
        if color.isValid():
            hex_val = color.name()
            self._apply_color_to_target(hex_val)
            self._sync_wheel_to_target()

    def _show_accent(self):
        for button, target, label, color in (
            (self.accent_button, "solid", "", self.accent),
            (self.grad_start_btn, "start", "Start", self.gradient_start),
            (self.grad_mid_btn, "mid", "Mid", self.gradient_mid),
            (self.grad_end_btn, "end", "End", self.gradient_end),
        ):
            chip = QPixmap(32, 32)
            chip.setDevicePixelRatio(2.0)
            chip.fill(Qt.GlobalColor.transparent)
            painter = QPainter(chip)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(0, 0, 16, 16), 4, 4)
            painter.end()
            button.setObjectName("StudioSwatch")
            button.setIcon(QIcon(chip))
            button.setIconSize(QSize(16, 16))
            button.setText(f"{label} {color.upper()}".strip())
            button.setProperty("colorSelected", self._active_wheel_target == target)
            button.style().unpolish(button)
            button.style().polish(button)
        rim = self.accent
        if self.accent_mode_combo.currentData() == "gradient":
            rim = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0.65,"
                f"stop:0 {self.gradient_start}, stop:0.52 {self.gradient_mid}, stop:1 {self.gradient_end})"
            )
        self.grad_preview.setStyleSheet(f"QFrame#StudioPreviewRim {{ background: {rim}; border-radius: 17px; }}")

    def _theme_values(self) -> dict[str, str]:
        return {
            "accent": self.accent,
            "accentMode": self.accent_mode_combo.currentData(),
            "gradientStart": self.gradient_start,
            "gradientMid": self.gradient_mid,
            "gradientEnd": self.gradient_end,
        }

    def _update_chat_bubble_button(self):
        chip = QPixmap(18, 18)
        chip.fill(QColor(self.chat_bubble_color))
        self.chat_bubble_button.setIcon(QIcon(chip))
        self.chat_bubble_button.setText(self.chat_bubble_color.upper())

    def _choose_chat_bubble_color(self):
        color = QColorDialog.getColor(QColor(self.chat_bubble_color), self, "User message color")
        if color.isValid():
            self.chat_bubble_color = color.name()
            self._update_chat_bubble_button()

    def values(self):
        lms_url = normalize_lmstudio_base_url(self.lmstudio_url.text().strip() or DEFAULT_LMSTUDIO_BASE_URL)
        data = {
            "name": self.name.text().strip(),
            **self._theme_values(),
            "defaultProvider": self.provider_combo.currentData(),
            "lmstudioBaseUrl": lms_url,
            "lmstudioModel": self.lmstudio_model.currentText().strip(),
            "memoryTurns": int(self.memory_turns.value()),
        }
        if IS_WINDOWS:
            data["customInstructions"] = self.custom_instructions.toPlainText().strip()[:8000]
            data["chatBubbleColor"] = self.chat_bubble_color
            data["weatherLocation"] = self.weather_location.text().strip()
            data["launchOnStartup"] = self.startup_checkbox.isChecked()
            data["lmstudioEnabled"] = self.lmstudio_enabled.isChecked()
            data["freeLlmApiEnabled"] = self.freellmapi_enabled.isChecked()
            data["freeLlmApiModel"] = self.freellmapi_model.currentData()
            data["allowAdminToolRequests"] = self.admin_tools_checkbox.isChecked()
            data["filterAppsWithGroq"] = self.filter_apps_with_groq.isChecked()
            data["fileSearchLevels"] = {
                folder: level.value() for folder, level in self.file_search_depths.items()
            }
            from step_desklet.win11.voice import DEFAULT_SHORTCUT
            from PyQt6.QtGui import QKeySequence

            data["voice"] = {
                "enabled": self.voice_enabled.isChecked(),
                "globalShortcut": self.voice_global.isChecked(),
                "shortcut": self.voice_shortcut.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                or DEFAULT_SHORTCUT,
                "review": self.voice_review.isChecked(),
                "spokenReplies": self.voice_spoken.isChecked(),
                "ttsEngine": self.voice_tts.currentData(),
                "ttsEngineExplicit": True,
                "microphone": self.voice_mic.currentData(),
                "speaker": self.voice_speaker.currentData(),
            }
        return data


class SkillManagerDialog(QDialog):
    """Manage zylo_skills/ enable/disable toggles, SHA-256 trust allowlist, and audit history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("liNi Skills, Trust & Execution History")
        self.resize(620, 620)
        self.setStyleSheet("""
            QDialog { background: #141e30; color: #f4f8ff; }
            QLabel { color: #e7f0ff; }
            QCheckBox { color: #f4f8ff; font-weight: 600; }
            QPushButton {
                color: #f6f9ff;
                background: rgba(255,255,255,18);
                border: 1px solid rgba(220,238,255,55);
                border-radius: 7px;
                padding: 5px 10px;
            }
            QPushButton:hover { background: rgba(255,255,255,34); }
        """)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Safe argument quoting alone does not make arbitrary PowerShell scripts safe. "
            "Disabled or untrusted skills (including edited scripts with changed SHA-256 hashes) "
            "are blocked automatically."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #cbd5e1; padding-bottom: 6px;")
        layout.addWidget(intro)

        self._skills_container = QWidget()
        self._skills_layout = QVBoxLayout(self._skills_container)
        self._skills_layout.setContentsMargins(0, 0, 6, 0)
        self._skills_layout.setSpacing(8)

        skills_scroll = QScrollArea(self)
        skills_scroll.setWidgetResizable(True)
        skills_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        skills_scroll.setWidget(self._skills_container)
        layout.addWidget(QLabel("Discovered zylo_skills/ Modules:"))
        layout.addWidget(skills_scroll, 2)

        hist_header = QHBoxLayout()
        hist_header.addWidget(QLabel("Skill Execution History:"))
        hist_header.addStretch(1)
        clear_hist_btn = QPushButton("Clear History")
        clear_hist_btn.clicked.connect(self._clear_history)
        hist_header.addWidget(clear_hist_btn)
        layout.addLayout(hist_header)

        self._history_label = QLabel()
        self._history_label.setWordWrap(True)
        self._history_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._history_label.setStyleSheet(
            "background: rgba(0,0,0,75); border-radius: 8px; padding: 8px; font-family: monospace; font-size: 11px;"
        )
        hist_scroll = QScrollArea(self)
        hist_scroll.setWidgetResizable(True)
        hist_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        hist_scroll.setWidget(self._history_label)
        layout.addWidget(hist_scroll, 1)

        footer = QHBoxLayout()
        open_folder_btn = QPushButton("Open zylo_skills/ Folder")
        open_folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_skills_directory())))
        )
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(open_folder_btn)
        footer.addWidget(refresh_btn)
        footer.addStretch(1)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self._refresh()

    def _refresh(self) -> None:
        while self._skills_layout.count():
            item = self._skills_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        skills = discover_skills(include_disabled=True)
        if not skills:
            self._skills_layout.addWidget(QLabel("No .ps1 skill modules found in zylo_skills/."))
        else:
            for skill in skills.values():
                card = QWidget()
                card.setStyleSheet(
                    "background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,28); border-radius: 8px;"
                )
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(10, 8, 10, 8)
                card_layout.setSpacing(4)

                top_row = QHBoxLayout()
                enable_cb = QCheckBox(f"{skill.skill_id} ({skill.script_path.name})")
                enable_cb.setChecked(skill.enabled)
                enable_cb.toggled.connect(lambda checked, sid=skill.skill_id: self._toggle_enabled(sid, checked))
                top_row.addWidget(enable_cb, 1)

                badge_color = "#75e0bd" if (skill.enabled and skill.trusted) else "#fbbf24"
                badge_text = (
                    "TRUSTED"
                    if skill.trusted
                    else "UNTRUSTED"
                )
                if not skill.enabled:
                    badge_text += " · DISABLED"
                badge = QLabel(badge_text)
                badge.setStyleSheet(f"color: {badge_color}; font-weight: 700; border: none;")
                top_row.addWidget(badge)

                trust_btn = QPushButton("Untrust" if skill.trusted else "Trust SHA-256")
                if skill.trusted:
                    trust_btn.clicked.connect(lambda _=False, sid=skill.skill_id: self._untrust(sid))
                else:
                    trust_btn.clicked.connect(lambda _=False, s=skill: self._trust(s))
                top_row.addWidget(trust_btn)
                card_layout.addLayout(top_row)

                meta = QLabel(
                    f"{skill.description}\nSHA-256: {skill.sha256[:16]}… · {skill.trust_reason}"
                )
                meta.setWordWrap(True)
                meta.setStyleSheet("color: #cbd5e1; font-size: 11px; border: none;")
                card_layout.addWidget(meta)

                self._skills_layout.addWidget(card)
        self._skills_layout.addStretch(1)

        history = read_skill_history(limit=40)
        if not history:
            self._history_label.setText("No skill executions recorded yet.")
        else:
            lines = []
            for entry in history:
                lines.append(
                    f"[{entry.get('timestamp', '')}] {entry.get('skill', '?')} · "
                    f"status={entry.get('status', '?')} exit={entry.get('exitCode', '?')} · "
                    f"sha256={str(entry.get('sha256', ''))[:10]} · {entry.get('error') or entry.get('reason') or ''}"
                )
            self._history_label.setText("\n".join(lines))

    def _toggle_enabled(self, skill_id: str, enabled: bool) -> None:
        set_skill_enabled(skill_id, enabled)
        self._refresh()

    def _trust(self, skill) -> None:
        trust_skill(skill)
        self._refresh()

    def _untrust(self, skill_id: str) -> None:
        untrust_skill(skill_id)
        self._refresh()

    def _clear_history(self) -> None:
        clear_skill_history()
        self._refresh()


def install_extension():
    if IS_WINDOWS:
        return
    subprocess.run(["gnome-extensions", "enable", "zylo-command@local"], check=True, capture_output=True, text=True)


def save_setup(dialog: SetupDialog) -> bool:
    values = dialog.values()
    if not values["name"]:
        QMessageBox.warning(dialog, "Name required", "Enter your name to continue.")
        dialog._select_section("General", dialog.name)
        return False

    previous = read_env_keys()
    api_key = dialog.key.text().strip() or previous["NVIDIA_API_KEY"]
    groq_key = dialog.groq_key.text().strip() or previous["GROQ_API_KEY"]
    tavily_key = dialog.tavily_key.text().strip() or previous["TAVILY_API_KEY"]
    lms_key = dialog.lmstudio_key.text().strip() or previous["LMSTUDIO_API_KEY"]
    free_key = (dialog.freellmapi_key.text().strip() or previous["FREELLMAPI_API_KEY"]) if IS_WINDOWS else None
    provider = values["defaultProvider"]
    if IS_WINDOWS and values["weatherLocation"] and weather_coordinates(values["weatherLocation"]) is None:
        QMessageBox.warning(dialog, "Invalid weather coordinates", "Enter a latitude and longitude separated by a comma.")
        dialog._select_section("General", dialog.weather_location)
        return False
    if IS_WINDOWS and values["voice"]["enabled"] and values["voice"]["globalShortcut"]:
        from step_desklet.win11.voice import win_shortcut

        try:
            win_shortcut(values["voice"]["shortcut"])
        except ValueError as error:
            QMessageBox.warning(dialog, "Choose a voice shortcut", str(error))
            dialog._select_section("Voice", dialog.voice_shortcut)
            return False

    if provider == "nim" and not api_key:
        QMessageBox.warning(
            dialog,
            "NVIDIA API key required",
            "Enter an NVIDIA key, or select another configured default provider.",
        )
        dialog._select_section("Providers", dialog.key)
        return False
    if provider == "groq" and not groq_key:
        QMessageBox.warning(
            dialog,
            "Groq API key required",
            "Enter a Groq key, or select another configured default provider.",
        )
        dialog._select_section("Providers", dialog.groq_key)
        return False
    if provider == "freellmapi" and not free_key:
        QMessageBox.warning(dialog, "Unified key required", "Enter the unified API key from FreeLLMAPI.")
        dialog._select_section("Providers", dialog.freellmapi_key)
        return False

    try:
        ensure_config_dir()
        write_json_file(get_settings_file(), values)
        write_provider(provider)
        write_env_keys(
            nvidia_key=api_key,
            groq_key=groq_key,
            tavily_key=tavily_key,
            lmstudio_base_url=values["lmstudioBaseUrl"],
            lmstudio_model=values["lmstudioModel"],
            lmstudio_api_key=lms_key,
            freellmapi_api_key=free_key,
            nvidia_backup_key=(
                "" if dialog.remove_nvidia_backup.isChecked()
                else dialog.nvidia_backup_key.text().strip() or previous["NVIDIA_BACKUP_API_KEY"]
            ),
            groq_backup_key=(
                "" if dialog.remove_groq_backup.isChecked()
                else dialog.groq_backup_key.text().strip() or previous["GROQ_BACKUP_API_KEY"]
            ),
        )
        if IS_WINDOWS:
            set_windows_startup(bool(values.get("launchOnStartup", False)))
        else:
            install_extension()
    except Exception as error:
        QMessageBox.critical(dialog, "liNi setup failed", str(error))
        return False
    return True


def run_setup_dialog(parent=None, show_ready_popup: bool = True) -> bool:
    dialog = SetupDialog(parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    if show_ready_popup:
        msg = (
            "liNi settings were saved."
            if IS_WINDOWS
            else "liNi is enabled on your desktop and will start with GNOME when you log in."
        )
        QMessageBox.information(dialog, "liNi is ready", msg)
    return True


def main():
    if IS_WINDOWS:
        from zylo_win11 import main as tray_main

        return tray_main()
    set_zylo_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName("liNi")
    app.setApplicationDisplayName("liNi Desktop Assistant")
    app.setWindowIcon(zylo_app_icon())
    ok = run_setup_dialog(show_ready_popup=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
