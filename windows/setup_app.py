#!/usr/bin/env python3
"""ZYLO first-run and settings launcher (cross-platform Linux GNOME & Windows 11 26H2)."""

import math
import re
import subprocess
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QConicalGradient,
    QDesktopServices,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from step_desklet.core.config import (
    ACCENTS,
    DEFAULT_LMSTUDIO_BASE_URL,
    DEFAULT_MEMORY_TURNS,
    GRADIENT_PRESETS,
    IS_WINDOWS,
    MAX_MEMORY_TURNS,
    MIN_MEMORY_TURNS,
    ensure_config_dir,
    get_accent_config,
    get_env_file,
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

API_KEY_URL = "https://build.nvidia.com/"
GROQ_API_KEY_URL = "https://console.groq.com/keys"
TAVILY_API_KEY_URL = "https://app.tavily.com/home"
LMSTUDIO_DOCS_URL = "https://lmstudio.ai/docs/app/api"


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
        status.setStyleSheet("color: #75e0bd; font-weight: 600" if is_saved else "color: #c0ccdf")
        status.setToolTip("This device has a saved key." if is_saved else "No key is saved on this device.")

        row = QHBoxLayout()
        row.addWidget(field, 1)
        row.addWidget(status)
        if help_button is not None:
            row.addWidget(help_button)
        return field, row

    def __init__(self, parent=None, *, show_desklet=None):
        super().__init__(parent)
        self.setWindowTitle("ZYLO Settings" if IS_WINDOWS else "Set up ZYLO")
        self.setMinimumWidth(540)
        self.resize(560, 740)
        self.setStyleSheet("""
            QDialog { background: #172336; color: #f4f8ff; }
            QLabel { color: #e7f0ff; }
            QCheckBox { color: #e7f0ff; spacing: 8px; }
            QLineEdit, QComboBox, QSpinBox {
                color: white;
                background: rgba(255,255,255,18);
                border: 1px solid rgba(220,238,255,65);
                border-radius: 8px;
                padding: 8px 9px;
            }
            QComboBox QAbstractItemView {
                background: #172336;
                color: white;
                selection-background-color: #3b2d5e;
            }
            QPushButton {
                color: #f6f9ff;
                background: rgba(255,255,255,18);
                border: 1px solid rgba(220,238,255,55);
                border-radius: 8px;
                padding: 8px 12px;
            }
            QPushButton:hover { background: rgba(255,255,255,32); }
        """)
        current = load_settings_config()
        saved_env = read_env_keys()
        accent_cfg = get_accent_config(current)
        self.is_activated = bool(current.get("name") and get_env_file().exists())

        self.name = QLineEdit(current.get("name", ""))
        self.name.setPlaceholderText("What should ZYLO call you?")

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("NVIDIA NIM (step-5-preview)", "nim")
        self.provider_combo.addItem("Groq (openai/gpt-oss-120b)", "groq")
        self.provider_combo.addItem("LM Studio (Local Server)", "lmstudio")
        active_provider = current.get("defaultProvider") or read_provider()
        idx = self.provider_combo.findData(active_provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)

        # LM Studio configuration fields
        self.lmstudio_url = QLineEdit(
            current.get("lmstudioBaseUrl") or saved_env.get("LMSTUDIO_BASE_URL") or DEFAULT_LMSTUDIO_BASE_URL
        )
        self.lmstudio_url.setPlaceholderText(DEFAULT_LMSTUDIO_BASE_URL)

        self.lmstudio_model = QComboBox()
        self.lmstudio_model.setEditable(True)
        saved_model = current.get("lmstudioModel") or saved_env.get("LMSTUDIO_MODEL") or ""
        if saved_model:
            self.lmstudio_model.addItem(saved_model)
        self.lmstudio_model.addItem("")
        self.lmstudio_model.setCurrentText(saved_model)
        if self.lmstudio_model.lineEdit():
            self.lmstudio_model.lineEdit().setPlaceholderText("Auto-detect loaded model, or enter model ID")

        self.memory_turns = QSpinBox()
        self.memory_turns.setRange(MIN_MEMORY_TURNS, MAX_MEMORY_TURNS)
        self.memory_turns.setValue(get_memory_turns(current) or DEFAULT_MEMORY_TURNS)
        self.memory_turns.setSuffix(" turns (persisted locally)")

        # Solid & Dynamic Gradient Accent controls
        self.accent_mode_combo = QComboBox()
        self.accent_mode_combo.addItem("Solid Accent Color", "solid")
        self.accent_mode_combo.addItem("Dynamic Gradient (Island Glow)", "gradient")
        mode_idx = self.accent_mode_combo.findData(accent_cfg["mode"])
        if mode_idx >= 0:
            self.accent_mode_combo.setCurrentIndex(mode_idx)
        self.accent_mode_combo.currentIndexChanged.connect(self._on_accent_mode_changed)

        self.gradient_preset_combo = QComboBox()
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

        self.grad_preview = QLabel("Dynamic Island / OriginOS Rim Preview")

        self._sync_gradient_preset_selection()
        self._sync_wheel_to_target()
        self._show_accent()

        self.startup_checkbox = QCheckBox("Launch ZYLO automatically when I sign in to Windows 11")
        if IS_WINDOWS:
            self.startup_checkbox.setChecked(
                current.get("launchOnStartup", is_windows_startup_enabled())
            )
        else:
            self.startup_checkbox.hide()

        title = QLabel("ZYLO Settings" if IS_WINDOWS else "Welcome to ZYLO")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: white")
        description = QLabel(
            "Personalize your desktop assistant. API keys, LM Studio endpoint, and conversation memory stay locally on this device."
        )
        description.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Your name", self.name)
        form.addRow("Default provider", self.provider_combo)

        if IS_WINDOWS:
            self.admin_tools_checkbox = QCheckBox("Allow ZYLO to request Windows administrator tools")
            self.admin_tools_checkbox.setChecked(current.get("allowAdminToolRequests") is True)
            form.addRow(self.admin_tools_checkbox)
            admin_hint = QLabel(
                "Ask ZYLO to open an administrator tool such as Event Viewer or Services. "
                "You approve each launch and Windows handles elevation."
            )
            admin_hint.setWordWrap(True)
            form.addRow(admin_hint)

        key_hint = QLabel("Leave any saved key's field blank to keep it. Click Save once to apply your changes.")
        key_hint.setWordWrap(True)
        form.addRow(key_hint)

        get_key = QPushButton("Get NVIDIA key")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_KEY_URL)))
        self.key, key_row = self._credential_row(saved_env["NVIDIA_API_KEY"], get_key)
        form.addRow("NVIDIA API key", key_row)

        get_groq_key = QPushButton("Get Groq key")
        get_groq_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GROQ_API_KEY_URL)))
        self.groq_key, groq_row = self._credential_row(saved_env["GROQ_API_KEY"], get_groq_key)
        form.addRow("Groq API key", groq_row)

        get_tavily_key = QPushButton("Get Tavily key")
        get_tavily_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(TAVILY_API_KEY_URL)))
        self.tavily_key, tavily_row = self._credential_row(saved_env["TAVILY_API_KEY"], get_tavily_key)
        form.addRow("Tavily search key", tavily_row)

        lms_url_row = QHBoxLayout()
        lms_url_row.addWidget(self.lmstudio_url, 1)
        lms_docs = QPushButton("LM Studio docs")
        lms_docs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(LMSTUDIO_DOCS_URL)))
        lms_url_row.addWidget(lms_docs)
        form.addRow("LM Studio URL", lms_url_row)

        lms_model_row = QHBoxLayout()
        lms_model_row.addWidget(self.lmstudio_model, 1)
        detect_btn = QPushButton("Detect models")
        detect_btn.clicked.connect(self._detect_lmstudio_models)
        lms_model_row.addWidget(detect_btn)
        form.addRow("LM Studio model", lms_model_row)

        self.lmstudio_key, lms_key_row = self._credential_row(saved_env["LMSTUDIO_API_KEY"])
        form.addRow("LM Studio token (optional)", lms_key_row)
        form.addRow("Memory history", self.memory_turns)
        form.addRow("Color style", self.accent_mode_combo)
        form.addRow("Solid accent", self.accent_button)
        form.addRow("Gradient preset", self.gradient_preset_combo)

        grad_stops_row = QHBoxLayout()
        grad_stops_row.addWidget(self.grad_start_btn, 1)
        grad_stops_row.addWidget(self.grad_mid_btn, 1)
        grad_stops_row.addWidget(self.grad_end_btn, 1)
        form.addRow("Gradient stops", grad_stops_row)

        wheel_box = QHBoxLayout()
        wheel_box.addWidget(self.color_wheel)
        wheel_controls = QVBoxLayout()
        wheel_controls.addWidget(self.wheel_target_label)
        wheel_controls.addWidget(QLabel("Brightness"))
        wheel_controls.addWidget(self.wheel_brightness)
        hex_row = QHBoxLayout()
        hex_row.addWidget(self.wheel_hex_input, 1)
        hex_row.addWidget(self.wheel_system_btn)
        wheel_controls.addLayout(hex_row)
        wheel_controls.addStretch(1)
        wheel_box.addLayout(wheel_controls, 1)
        form.addRow("Colour wheel", wheel_box)

        form.addRow("Gradient preview", self.grad_preview)

        self.skills_manager_btn = QPushButton("Manage Skills, Trust & History…")
        self.skills_manager_btn.clicked.connect(self._open_skill_manager)
        form.addRow("PowerShell skills", self.skills_manager_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if show_desklet is not None:
            desklet_button = buttons.addButton("Show Desklet", QDialogButtonBox.ButtonRole.ActionRole)
            desklet_button.clicked.connect(lambda: show_desklet(self._theme_values()))

        scroll_inner = QWidget()
        scroll_layout = QVBoxLayout(scroll_inner)
        scroll_layout.setContentsMargins(2, 2, 8, 2)
        scroll_layout.addLayout(form)
        if IS_WINDOWS:
            scroll_layout.addWidget(self.startup_checkbox)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(scroll_inner)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        if self.is_activated:
            activated = QLabel("ZYLO is already configured on this device. You can update your settings below.")
            activated.setWordWrap(True)
            activated.setStyleSheet("color: #75e0bd; font-weight: 600; padding: 6px 0")
            layout.addWidget(activated)
        layout.addWidget(description)
        layout.addWidget(scroll_area, 1)
        layout.addWidget(buttons)

        self._on_accent_mode_changed()

    def accept(self):
        if save_setup(self):
            super().accept()

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
            "solid": "Editing: Solid Accent",
            "start": "Editing: Gradient Start",
            "mid": "Editing: Gradient Mid",
            "end": "Editing: Gradient End",
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
        color = QColorDialog.getColor(current, parent=self, title="Choose ZYLO color")
        if color.isValid():
            hex_val = color.name()
            self._apply_color_to_target(hex_val)
            self._sync_wheel_to_target()

    def _show_accent(self):
        def _btn_style(hex_col: str, active: bool) -> str:
            border = "2px solid #ffffff" if active else "1px solid rgba(255,255,255,70)"
            c = QColor(hex_col)
            luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            fg = "#142235" if luma > 135 else "#f8fafc"
            return f"background: {hex_col}; color: {fg}; font-weight: 700; border-radius: 8px; padding: 6px; border: {border};"

        self.accent_button.setText(self.accent)
        self.accent_button.setStyleSheet(_btn_style(self.accent, self._active_wheel_target == "solid"))
        self.grad_start_btn.setText(f"Start {self.gradient_start}")
        self.grad_start_btn.setStyleSheet(_btn_style(self.gradient_start, self._active_wheel_target == "start"))
        self.grad_mid_btn.setText(f"Mid {self.gradient_mid}")
        self.grad_mid_btn.setStyleSheet(_btn_style(self.gradient_mid, self._active_wheel_target == "mid"))
        self.grad_end_btn.setText(f"End {self.gradient_end}")
        self.grad_end_btn.setStyleSheet(_btn_style(self.gradient_end, self._active_wheel_target == "end"))
        self.grad_preview.setStyleSheet(
            f"background: #1b132b; color: #f5f9ff; font-weight: 600; padding: 8px 12px; border-radius: 10px; "
            f"border: 2px solid qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {self.gradient_start}, stop:0.5 {self.gradient_mid}, stop:1 {self.gradient_end});"
        )

    def _theme_values(self) -> dict[str, str]:
        return {
            "accent": self.accent,
            "accentMode": self.accent_mode_combo.currentData(),
            "gradientStart": self.gradient_start,
            "gradientMid": self.gradient_mid,
            "gradientEnd": self.gradient_end,
        }

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
            data["launchOnStartup"] = self.startup_checkbox.isChecked()
            data["allowAdminToolRequests"] = self.admin_tools_checkbox.isChecked()
        return data


class SkillManagerDialog(QDialog):
    """Manage zylo_skills/ enable/disable toggles, SHA-256 trust allowlist, and audit history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZYLO Skills, Trust & Execution History")
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
        return False

    previous = read_env_keys()
    api_key = dialog.key.text().strip() or previous["NVIDIA_API_KEY"]
    groq_key = dialog.groq_key.text().strip() or previous["GROQ_API_KEY"]
    tavily_key = dialog.tavily_key.text().strip() or previous["TAVILY_API_KEY"]
    lms_key = dialog.lmstudio_key.text().strip() or previous["LMSTUDIO_API_KEY"]
    provider = values["defaultProvider"]

    if provider == "nim" and not api_key:
        QMessageBox.warning(
            dialog,
            "NVIDIA API key required",
            "Enter an NVIDIA key, or switch Default provider to Groq or LM Studio.",
        )
        return False
    if provider == "groq" and not groq_key:
        QMessageBox.warning(
            dialog,
            "Groq API key required",
            "Enter a Groq key, or switch Default provider to NVIDIA NIM or LM Studio.",
        )
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
        )
        if IS_WINDOWS:
            set_windows_startup(bool(values.get("launchOnStartup", False)))
        else:
            install_extension()
    except Exception as error:
        QMessageBox.critical(dialog, "ZYLO setup failed", str(error))
        return False
    return True


def run_setup_dialog(parent=None, show_ready_popup: bool = True) -> bool:
    dialog = SetupDialog(parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    if show_ready_popup:
        msg = (
            "ZYLO settings were saved."
            if IS_WINDOWS
            else "ZYLO is enabled on your desktop and will start with GNOME when you log in."
        )
        QMessageBox.information(dialog, "ZYLO is ready", msg)
    return True


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ZYLO")
    ok = run_setup_dialog(show_ready_popup=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
