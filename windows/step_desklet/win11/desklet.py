"""PyQt6 Windows 11 26H2 floating Acrylic desklet with Dynamic Island / OriginOS gradient glow."""

import os
import math
from typing import Optional

from dotenv import dotenv_values
from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QConicalGradient,
    QCursor,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from step_desklet.core.admin_tools import ADMIN_TOOLS
from step_desklet.core.config import (
    DEFAULT_DESKLET_WIDTH,
    DEFAULT_DESKLET_X,
    DEFAULT_DESKLET_Y,
    DEFAULT_LMSTUDIO_BASE_URL,
    MAX_DESKLET_HEIGHT,
    MAX_DESKLET_WIDTH,
    MIN_DESKLET_HEIGHT,
    MIN_DESKLET_WIDTH,
    get_accent_config,
    get_conversation_file,
    get_env_file,
    get_layout_file,
    get_memory_turns,
    load_layout_config,
    load_settings_config,
    read_provider,
    refresh_local_date,
    write_json_file,
    write_provider,
)
from step_desklet.win11.admin_tools import launch_admin_tool
from step_desklet.win11.dwm import (
    apply_tool_window_exstyle,
    apply_win11_acrylic,
    show_window_above_wallpaper,
)
from step_desklet.win11.focus_monitor import wallpaper_covering_window
from step_desklet.win11.request_process import DeskletRequest


def _outline_icon(kind: str, color: str) -> QPixmap:
    pixmap = QPixmap(36, 36)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.35)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    path = QPainterPath()
    if kind == "clear":
        painter.drawArc(QRectF(2.6, 2.8, 12.8, 12.8), 142 * 16, -307 * 16)
        path.moveTo(3, 2.8)
        path.lineTo(3, 6.7)
        path.lineTo(6.9, 6.7)
    elif kind in ("locked", "unlocked"):
        painter.drawRoundedRect(QRectF(2.9, 7.9, 12.2, 7.5), 1.6, 1.6)
        path.moveTo(5.7, 8)
        path.lineTo(5.7, 5.5)
        path.cubicTo(5.7, 1, 12.3, 1, 12.3, 5.5)
        if kind == "locked":
            path.lineTo(12.3, 8)
    elif kind == "send":
        path.moveTo(2.3, 7.5)
        path.lineTo(15.4, 2.6)
        path.lineTo(10.4, 15.6)
        path.lineTo(7.9, 10.2)
        path.closeSubpath()
        path.moveTo(7.9, 10.2)
        path.lineTo(15.4, 2.6)
    elif kind == "resize":
        path.moveTo(4.3, 13.7)
        path.lineTo(13.7, 4.3)
        path.moveTo(4.3, 9.8)
        path.lineTo(4.3, 13.7)
        path.lineTo(8.2, 13.7)
        path.moveTo(9.8, 4.3)
        path.lineTo(13.7, 4.3)
        path.lineTo(13.7, 8.2)
    painter.drawPath(path)
    painter.end()
    return pixmap


class GradientGlassCard(QFrame):
    """Paint the glass tint, rim and traveling glow on one alpha surface."""

    GLOW_MARGIN = 12
    CORNER_RADIUS = 19.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors = (QColor("#a78bfa"), QColor("#38bdf8"), QColor("#f472b6"))
        self.phase_deg = 0.0
        self.active_boost = False

    def configure_colors(self, start: str, mid: str, end: str) -> None:
        self.colors = (QColor(start), QColor(mid), QColor(end))
        self.update()

    def _rim_gradient(self, center: QPointF, opacity: int, pulse: float = 0.0) -> QConicalGradient:
        gradient = QConicalGradient(center, self.phase_deg)
        start, mid, end = self.colors
        stops = (
            (0.0, start, 0.0),
            (0.13, start, 0.2),
            (0.21, mid, 1.0),
            (0.27, mid, 0.55),
            (0.35, mid, 0.0),
            (0.7, end, 0.0),
            (1.0, start, 0.0),
        )
        if not pulse:
            stops = ((0.0, start, 0.0), (0.35, mid, 0.0), (0.7, end, 0.0), (1.0, start, 0.0))
        for stop, color, peak in stops:
            tint = QColor(color)
            tint.setAlpha(min(255, round(opacity * (1.0 + peak * pulse))))
            gradient.setColorAt(stop, tint)
        return gradient

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = self.GLOW_MARGIN
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        card = QPainterPath()
        card.addRoundedRect(rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        # Leave the center clear of the halo and fade it out before it reaches the window edge.
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        painter.save()
        painter.setClipPath(outside.subtracted(card))
        beat = 0.5 + 0.5 * math.sin(math.radians(self.phase_deg * 2))
        pulse = (1.8 if self.active_boost else 1.1) + 1.3 * beat
        intensity = 1.5 if self.active_boost else 1.0
        for width, opacity in ((22, 6), (18, 8), (14, 10), (10, 13), (6, 17), (3, 22)):
            light = self._rim_gradient(rect.center(), round(opacity * intensity), pulse)
            painter.strokePath(card, QPen(light, width))
        painter.restore()

        painter.fillPath(card, QColor(23, 21, 37, 216))
        glass_tint = QLinearGradient(rect.topLeft(), rect.bottomRight())
        for stop, color, opacity in zip((0.0, 0.48, 1.0), self.colors, (56, 26, 45)):
            tint = QColor(color)
            tint.setAlpha(opacity)
            glass_tint.setColorAt(stop, tint)
        painter.fillPath(card, glass_tint)
        painter.strokePath(card, QPen(self._rim_gradient(rect.center(), 245), 1.5 if self.active_boost else 1.3))
        painter.end()


class GradientInputShell(QFrame):
    """Draw the input border with one gradient mapped across the whole outline."""

    def __init__(self, card: GradientGlassCard):
        super().__init__(card)
        self._card = card
        self.setContentsMargins(1, 1, 1, 1)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        outline = QPainterPath()
        outline.addRoundedRect(bounds, 12.5, 12.5)
        gradient = QLinearGradient(bounds.topLeft(), bounds.topRight())
        for position, color in zip((0.0, 0.5, 1.0), self._card.colors):
            gradient.setColorAt(position, color)
        painter.fillPath(outline, QColor(9, 13, 22, 232))
        painter.strokePath(outline, QPen(gradient, 1.0))
        painter.end()


class ZyloDeskletWindow(QWidget):
    """Floating Windows 11 Acrylic glass desklet for ZYLO Desktop Assistant."""

    request_setup = pyqtSignal()
    provider_changed = pyqtSignal(str)
    lock_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ZyloDeskletWindow")
        self.setWindowTitle("ZYLO")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._settings = load_settings_config()
        self._layout_data = load_layout_config()
        self._provider = read_provider()
        self._locked = bool(self._layout_data["locked"])
        self._interacting = False
        self._drag_mode: Optional[str] = None
        self._drag_start_global = QPoint()
        self._start_pos = QPoint()
        self._start_size = QSize()
        self._user_height: Optional[int] = self._layout_data["height"]
        self._anchor_pos = QPoint(self._layout_data["x"], self._layout_data["y"])

        self._request: Optional[DeskletRequest] = None
        self._admin_prompt: Optional[QMessageBox] = None
        self._busy = False
        self._shutting_down = False

        self._autoclear_timer = QTimer(self)
        self._autoclear_timer.setSingleShot(True)
        self._autoclear_timer.timeout.connect(self._clear_response_only)

        self._provider_status_timer = QTimer(self)
        self._provider_status_timer.setSingleShot(True)
        self._provider_status_timer.timeout.connect(self._hide_status_banner)

        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(40)
        self._glow_timer.timeout.connect(self._advance_gradient_phase)

        self._build_ui()
        self.apply_theme()
        self._restore_geometry()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.card = GradientGlassCard(self)
        self.card.setObjectName("ZyloGlassCard")
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        margin = self.card.GLOW_MARGIN
        card_layout.setContentsMargins(margin + 15, margin + 13, margin + 15, margin + 7)
        card_layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)

        self.handle_widget = QWidget(self.card)
        self.handle_widget.setCursor(
            QCursor(Qt.CursorShape.ArrowCursor if self._locked else Qt.CursorShape.OpenHandCursor)
        )
        handle_layout = QVBoxLayout(self.handle_widget)
        handle_layout.setContentsMargins(0, 0, 8, 0)
        handle_layout.setSpacing(1)

        self.title_label = QLabel(self._settings.get("name") or "ZYLO", self.handle_widget)
        self.title_label.setObjectName("ZyloTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.subtitle_label = QLabel("SYSTEM COMMAND", self.handle_widget)
        self.subtitle_label.setObjectName("ZyloSubtitle")
        self.subtitle_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        handle_layout.addWidget(self.title_label)
        handle_layout.addWidget(self.subtitle_label)

        self.handle_widget.installEventFilter(self)
        header.addWidget(self.handle_widget, 1)

        actions = QHBoxLayout()
        actions.setSpacing(3)

        self.clear_btn = QPushButton(self.card)
        self.clear_btn.setObjectName("ZyloIconButton")
        self.clear_btn.setToolTip("Stop work and clear response")
        self.clear_btn.setAccessibleName("Stop work and clear response")
        self.clear_btn.setIconSize(QSize(16, 16))
        self.clear_btn.setFixedSize(28, 26)
        self.clear_btn.clicked.connect(self.clear_all)
        actions.addWidget(self.clear_btn)

        provider_badge = {"groq": "G", "lmstudio": "L"}.get(self._provider, "N")
        self.model_btn = QPushButton(provider_badge, self.card)
        self.model_btn.setObjectName("ZyloProviderButton")
        self.model_btn.setToolTip("Switch AI provider (NVIDIA NIM / Groq / LM Studio)")
        self.model_btn.setFixedSize(28, 26)
        self.model_btn.clicked.connect(self.toggle_provider)
        actions.addWidget(self.model_btn)

        self.lock_btn = QPushButton(self.card)
        self.lock_btn.setObjectName("ZyloIconButton")
        self.lock_btn.setIconSize(QSize(16, 16))
        self._sync_lock_button()
        self.lock_btn.setFixedSize(28, 26)
        self.lock_btn.clicked.connect(self.toggle_lock)
        actions.addWidget(self.lock_btn)

        header.addLayout(actions)
        card_layout.addLayout(header)

        self.message_area = QScrollArea(self.card)
        self.message_area.setObjectName("ZyloMessages")
        self.message_area.setFrameShape(QFrame.Shape.NoFrame)
        self.message_area.setWidgetResizable(True)
        self.message_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.message_panel = QWidget()
        self.message_panel.setObjectName("ZyloMessagePanel")
        message_layout = QVBoxLayout(self.message_panel)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(0)
        self.message_area.setWidget(self.message_panel)
        self.message_area.setFixedHeight(0)
        self.message_area.hide()
        card_layout.addWidget(self.message_area)

        self.response_label = QLabel(self.message_panel)
        self.response_label.setObjectName("ZyloResponse")
        self.response_label.setWordWrap(True)
        self.response_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.response_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.response_label.hide()
        message_layout.addWidget(self.response_label)

        # Status label
        self.status_label = QLabel(self.message_panel)
        self.status_label.setObjectName("ZyloStatus")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        message_layout.addWidget(self.status_label)

        card_layout.addStretch(1)

        # Input shell
        self.input_shell = GradientInputShell(self.card)
        self.input_shell.setObjectName("ZyloInputShell")
        input_layout = QHBoxLayout(self.input_shell)
        self.input_shell.setFixedHeight(34)
        input_layout.setContentsMargins(10, 3, 5, 3)
        input_layout.setSpacing(6)

        self.prompt_input = QLineEdit(self.input_shell)
        self.prompt_input.setObjectName("ZyloInput")
        self.prompt_input.setPlaceholderText("Ask ZYLO")
        self.prompt_input.returnPressed.connect(self.submit_prompt)
        input_layout.addWidget(self.prompt_input, 1)

        self.send_btn = QPushButton(self.input_shell)
        self.send_btn.setObjectName("ZyloSend")
        self.send_btn.setFixedSize(29, 23)
        self.send_btn.setIconSize(QSize(15, 15))
        self.send_btn.setAccessibleName("Send prompt")
        self.send_btn.setToolTip("Send prompt")
        self.send_btn.clicked.connect(self.submit_prompt)
        input_layout.addWidget(self.send_btn)

        card_layout.addWidget(self.input_shell)

        # Footer resize grip
        self.footer_widget = QWidget(self.card)
        footer_layout = QHBoxLayout(self.footer_widget)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addStretch(1)
        self.resize_handle = QLabel(self.footer_widget)
        self.resize_handle.setObjectName("ZyloResizeHandle")
        self.resize_handle.setFixedSize(20, 12)
        self.resize_handle.setPixmap(_outline_icon("resize", "#c6b9da").scaled(
            24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))
        self.resize_handle.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.resize_handle.setToolTip("Drag to resize panel")
        self.resize_handle.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        self.resize_handle.installEventFilter(self)
        footer_layout.addWidget(self.resize_handle)
        card_layout.addWidget(self.footer_widget)
        self.footer_widget.setVisible(not self._locked)

    def _advance_gradient_phase(self) -> None:
        if not self._busy:
            return
        self.card.phase_deg = (self.card.phase_deg - 4.1) % 360.0
        self.card.update()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.card.active_boost = busy
        if busy and self.isVisible():
            self._glow_timer.start()
        else:
            self._glow_timer.stop()
        self.card.update()

    def _sync_lock_button(self) -> None:
        color = self.card.colors[2].name()
        self.lock_btn.setIcon(QIcon(_outline_icon("locked" if self._locked else "unlocked", color)))
        action = "Unlock panel position and size" if self._locked else "Lock panel position and size"
        self.lock_btn.setToolTip(action)
        self.lock_btn.setAccessibleName(action)

    def apply_theme(self, preview: dict[str, str] | None = None) -> None:
        cfg = get_accent_config(self._settings if preview is None else preview)
        if cfg["mode"] == "gradient":
            start, mid, end = cfg["gradientStart"], cfg["gradientMid"], cfg["gradientEnd"]
        else:
            start = mid = end = cfg["accent"]

        self.card.configure_colors(start, mid, end)
        self.input_shell.update()
        self.clear_btn.setIcon(QIcon(_outline_icon("clear", start)))
        self.send_btn.setIcon(QIcon(_outline_icon("send", "#101421")))
        self._sync_lock_button()
        start_color = QColor(start)
        hover = f"rgba({start_color.red()}, {start_color.green()}, {start_color.blue()}, 36)"
        send_bg = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {start}, stop:0.54 {mid}, stop:1 {end})"
        )

        self.setStyleSheet(
            f"""
            QLabel#ZyloTitle {{
                color: #f4f6ff;
                font-family: "Segoe UI Variable Display", "Segoe UI", sans-serif;
                font-size: 15px;
                font-weight: 750;
                letter-spacing: 1px;
                background: transparent;
            }}
            QLabel#ZyloSubtitle {{
                color: #aab7d1;
                font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
                background: transparent;
            }}
            QPushButton#ZyloIconButton, QPushButton#ZyloProviderButton {{
                border-radius: 9px;
                background: transparent;
                border: none;
            }}
            QPushButton#ZyloProviderButton {{
                color: {mid};
                font-size: 13px;
                font-weight: 750;
            }}
            QPushButton#ZyloIconButton:hover, QPushButton#ZyloProviderButton:hover {{
                background-color: {hover};
            }}
            QScrollArea#ZyloMessages, QWidget#ZyloMessagePanel {{
                background: transparent;
                border: none;
            }}
            QLabel#ZyloStatus {{
                color: #d1e5ff;
                font-size: 11px;
                padding-top: 3px;
                background: transparent;
            }}
            QLabel#ZyloResponse {{
                color: #edf5ff;
                font-size: 13.5px;
                padding-top: 3px;
                background: transparent;
            }}
            QLineEdit#ZyloInput {{
                color: #f7fbff;
                background: transparent;
                border: none;
                font-size: 12.5px;
                padding: 0px;
            }}
            QPushButton#ZyloSend {{
                background: {send_bg};
                border-radius: 11px;
                border: none;
            }}
            QPushButton#ZyloSend:hover {{
                background: #eef3ff;
            }}
            QPushButton#ZyloSend:disabled {{
                background: rgba(220, 202, 255, 104);
            }}
            """
        )
        palette = self.prompt_input.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#97a9c3"))
        self.prompt_input.setPalette(palette)

    def _available_screen_rect(self, point: Optional[QPoint] = None):
        if point is not None:
            scr = QApplication.screenAt(point)
            if scr is not None:
                return scr.availableGeometry()
        scr = self.screen() or QApplication.primaryScreen()
        return scr.availableGeometry() if scr else None

    def _clamp_position(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        avail = self._available_screen_rect(QPoint(x + width // 2, y + height // 2))
        if not avail:
            return x, y
        max_x = max(avail.left(), avail.right() - width + 1)
        max_y = max(avail.top(), avail.bottom() - height + 1)
        clamped_x = max(avail.left(), min(x, max_x))
        clamped_y = max(avail.top(), min(y, max_y))
        return clamped_x, clamped_y

    def _restore_geometry(self) -> None:
        width = max(MIN_DESKLET_WIDTH, min(MAX_DESKLET_WIDTH, int(self._layout_data["width"])))
        self.setFixedWidth(width)
        self._fit_height()

    def reset_layout(self) -> None:
        self._user_height = None
        self._anchor_pos = QPoint(DEFAULT_DESKLET_X, DEFAULT_DESKLET_Y)
        self.setFixedWidth(DEFAULT_DESKLET_WIDTH)
        self._fit_height()
        self._save_layout()

    def _save_layout(self) -> None:
        payload = {
            "x": self._anchor_pos.x(),
            "y": self._anchor_pos.y(),
            "width": self.width(),
            "height": self._user_height,
            "locked": self._locked,
        }
        write_json_file(get_layout_file(), payload)

    def _fit_height(self) -> None:
        layout = self.card.layout()
        has_message = not self.response_label.isHidden() or not self.status_label.isHidden()
        self.message_area.setVisible(has_message)
        layout.invalidate()
        width = self.width()
        base_height = layout.sizeHint().height()
        anchor = self._anchor_pos
        screen = self._available_screen_rect(anchor + QPoint(width // 2, 0))
        max_height = min(MAX_DESKLET_HEIGHT, screen.height()) if screen is not None else MAX_DESKLET_HEIGHT
        if has_message:
            base_height -= self.message_area.minimumHeight()
            margins = layout.contentsMargins()
            content_width = max(1, width - margins.left() - margins.right())
            message_layout = self.message_panel.layout()
            message_layout.invalidate()
            message_height = message_layout.totalHeightForWidth(content_width)
            message_height = min(message_height, max(0, max_height - base_height))
            self.message_area.setFixedHeight(message_height)
            base_height += message_height

        target_height = min(max_height, max(base_height, self._user_height or 0, MIN_DESKLET_HEIGHT))
        self.setFixedHeight(target_height)
        layout.activate()
        x, y = self._clamp_position(anchor.x(), anchor.y(), width, target_height)
        if self.pos() != QPoint(x, y):
            self.move(x, y)
        self.card.update()

    def reload_settings(self) -> None:
        self._settings = load_settings_config()
        self._provider = read_provider()
        self.model_btn.setText({"groq": "G", "lmstudio": "L"}.get(self._provider, "N"))
        self.title_label.setText(self._settings.get("name") or "ZYLO")
        self.apply_theme()
        self._fit_height()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        apply_tool_window_exstyle(hwnd)
        # Qt provides the alpha surface, including rounded corners and the halo outside the card.
        apply_win11_acrylic(hwnd, hide_dwm_border=True, enable_blur=False)
        if self._busy:
            self._glow_timer.start()

    def hideEvent(self, event) -> None:
        self._glow_timer.stop()
        super().hideEvent(event)

    def bring_to_desktop(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()

    def restore_above_wallpaper(self) -> None:
        if self.isMinimized():
            return
        hwnd = int(self.winId())
        wallpaper = wallpaper_covering_window(hwnd)
        if wallpaper:
            self.show()
            show_window_above_wallpaper(hwnd, wallpaper)

    def toggle_lock(self) -> None:
        self._locked = not self._locked
        if self._locked:
            self._drag_mode = None
            self._interacting = False
        self._sync_lock_button()
        self.footer_widget.setVisible(not self._locked)
        self.handle_widget.setCursor(
            QCursor(Qt.CursorShape.ArrowCursor if self._locked else Qt.CursorShape.OpenHandCursor)
        )
        self._fit_height()
        self._save_layout()
        self.lock_changed.emit(self._locked)

    def toggle_provider(self) -> None:
        self._provider_status_timer.stop()
        order = ["nim", "groq", "lmstudio"]
        idx = order.index(self._provider) if self._provider in order else 0
        self._provider = order[(idx + 1) % len(order)]
        self.model_btn.setText({"groq": "G", "lmstudio": "L"}.get(self._provider, "N"))
        write_provider(self._provider)
        if self._provider == "groq":
            label = "Groq · GPT OSS 120B selected"
        elif self._provider == "lmstudio":
            lms_model = self._settings.get("lmstudioModel") or "auto-loaded model"
            label = f"LM Studio · {lms_model} selected"
        else:
            label = "NVIDIA · Nemotron 3.5 Super selected"
        self.status_label.setText(label)
        self.status_label.show()
        self._fit_height()
        self._provider_status_timer.start(2200)
        self.provider_changed.emit(self._provider)

    def clear_all(self) -> None:
        self._cancel_request()
        self._autoclear_timer.stop()
        self._provider_status_timer.stop()
        self.response_label.clear()
        self.response_label.hide()
        self.status_label.clear()
        self.status_label.hide()
        self.prompt_input.clear()
        self._set_busy(False)
        if not self._shutting_down:
            self.prompt_input.setEnabled(True)
            self.send_btn.setEnabled(True)
            self.prompt_input.setFocus()
        self._fit_height()

    def _cancel_request(self) -> None:
        request, self._request = self._request, None
        if request is not None:
            request.cancel()
        prompt, self._admin_prompt = self._admin_prompt, None
        if prompt is not None:
            prompt.reject()

    def _clear_response_only(self) -> None:
        self.response_label.clear()
        self.response_label.hide()
        self._fit_height()

    def _hide_status_banner(self) -> None:
        if not self._busy:
            self.status_label.hide()
            self._fit_height()

    def submit_prompt(self) -> None:
        prompt = self.prompt_input.text().strip()
        if not prompt or self._busy or self._shutting_down:
            return
        refresh_local_date()
        self._provider_status_timer.stop()
        self._autoclear_timer.stop()

        env_path = get_env_file()
        if env_path.exists():
            os.environ.update({
                key: value for key, value in dotenv_values(env_path).items()
                if value is not None and key not in {"NVIDIA_BACKUP_API_KEY", "GROQ_BACKUP_API_KEY"}
            })

        self._set_busy(True)
        self.prompt_input.clear()
        self.prompt_input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.response_label.hide()
        self.status_label.setText("Thinking…")
        self.status_label.show()
        self._fit_height()

        initial_dir = os.getenv("STEP_DESKLET_WORKING_DIR", os.path.expanduser("~"))
        conv_path = str(get_conversation_file())
        max_history_messages = get_memory_turns(self._settings) * 2
        worker_options = {
            "initial_working_dir": initial_dir,
            "conversation_path": conv_path,
            "max_history_messages": max_history_messages,
            "allow_admin_tool_requests": self._settings.get("allowAdminToolRequests") is True,
        }

        request = DeskletRequest(self)
        self._request = request
        request.status.connect(self._on_worker_status)
        request.outcome.connect(self._on_request_outcome)
        request.start_request({
            "prompt": prompt,
            "provider": self._provider,
            "options": worker_options,
            "base_url": (
                os.getenv("LMSTUDIO_BASE_URL")
                or self._settings.get("lmstudioBaseUrl")
                or DEFAULT_LMSTUDIO_BASE_URL
            ),
            "model": os.getenv("LMSTUDIO_MODEL") or self._settings.get("lmstudioModel") or "",
        })

    def _on_request_outcome(self, event: str, value: str) -> None:
        if self.sender() is not self._request:
            return
        self._request = None
        if event == "response":
            self._on_worker_response(value)
        elif event == "admin_tool":
            self._on_admin_tool_requested(value)
        elif event == "cancelled":
            self._on_worker_cancelled()
        else:
            self._finish_worker_error(value)

    def _on_worker_status(self, text: str) -> None:
        if self._shutting_down:
            return
        self.status_label.setText(text)
        self.status_label.show()
        self._fit_height()

    def _on_worker_response(self, text: str) -> None:
        self._set_busy(False)
        if self._shutting_down:
            return
        self.prompt_input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_label.hide()
        self.response_label.setText(text)
        self.response_label.show()
        self._fit_height()

        word_count = len(text.strip().split())
        delay_ms = max(6000, min(120000, 3500 + word_count * 220))
        self._autoclear_timer.start(delay_ms)
        self.prompt_input.setFocus()

    def _on_admin_tool_requested(self, tool_id: str) -> None:
        if self._shutting_down:
            return

        tool = ADMIN_TOOLS.get(tool_id)
        if tool is None or load_settings_config().get("allowAdminToolRequests") is not True:
            self._on_worker_response("This administrator tool request is unavailable.")
            return
        prompt = QMessageBox(
            QMessageBox.Icon.Question,
            "Windows administrator tool",
            f"Open {tool.label} with administrator access? Windows may show a User Account Control prompt.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        prompt.setDefaultButton(QMessageBox.StandardButton.No)
        prompt.setWindowModality(Qt.WindowModality.NonModal)
        prompt.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._admin_prompt = prompt
        prompt.finished.connect(lambda choice: self._finish_admin_prompt(tool_id, prompt, choice))
        self._on_worker_status("Waiting for administrator approval.")
        prompt.adjustSize()
        area = self._available_screen_rect(self.frameGeometry().center())
        if area is not None:
            x = min(self.x(), area.right() - prompt.width())
            below = self.geometry().bottom() + 8
            y = below if below + prompt.height() <= area.bottom() else self.y() - prompt.height() - 8
            prompt.move(max(x, area.left()), max(y, area.top()))
        prompt.show()

    def _finish_admin_prompt(self, tool_id: str, prompt: QMessageBox, choice: int) -> None:
        if self._admin_prompt is not prompt:
            return
        self._admin_prompt = None
        if choice != QMessageBox.StandardButton.Yes or load_settings_config().get("allowAdminToolRequests") is not True:
            self._on_worker_response("Administrator tool launch cancelled.")
            return
        tool = ADMIN_TOOLS[tool_id]
        try:
            launched = launch_admin_tool(tool_id, int(self.winId()))
        except OSError as error:
            self._on_worker_response(f"Could not open {tool.label}: {error}")
        else:
            message = f"Requested Windows to open {tool.label}." if launched else "Administrator tool launch cancelled."
            self._on_worker_response(message)

    def _on_worker_cancelled(self) -> None:
        self._set_busy(False)
        if self._shutting_down:
            return
        self.prompt_input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_label.setText("Cancelled.")
        self.status_label.show()
        self._fit_height()
        self._provider_status_timer.start(1800)
        self.prompt_input.setFocus()

    def _finish_worker_error(self, error_text: str) -> None:
        self._set_busy(False)
        if self._shutting_down:
            return
        self.prompt_input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_label.hide()
        self.response_label.setText(f"Error: {error_text}")
        self.response_label.show()
        self._fit_height()
        self.prompt_input.setFocus()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.prompt_input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.clear_all()
        for request in self.findChildren(DeskletRequest):
            request.waitForFinished(1000)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.handle_widget and not self._locked:
            if isinstance(event, QMouseEvent):
                if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    self._drag_mode = "move"
                    self._interacting = True
                    self._drag_start_global = event.globalPosition().toPoint()
                    self._start_pos = self.pos()
                    self.handle_widget.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                    return True
                if event.type() == event.Type.MouseMove and self._drag_mode == "move":
                    delta = event.globalPosition().toPoint() - self._drag_start_global
                    target = self._start_pos + delta
                    cx, cy = self._clamp_position(target.x(), target.y(), self.width(), self.height())
                    self._anchor_pos = QPoint(cx, cy)
                    self.move(cx, cy)
                    return True
                if event.type() == event.Type.MouseButtonRelease and self._drag_mode == "move":
                    self._drag_mode = None
                    self._interacting = False
                    self.handle_widget.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
                    self._save_layout()
                    return True
        if obj is self.resize_handle and not self._locked:
            if isinstance(event, QMouseEvent):
                if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    self._drag_mode = "resize"
                    self._interacting = True
                    self._drag_start_global = event.globalPosition().toPoint()
                    self._start_size = self.size()
                    return True
                if event.type() == event.Type.MouseMove and self._drag_mode == "resize":
                    delta = event.globalPosition().toPoint() - self._drag_start_global
                    avail = self._available_screen_rect(self.pos())
                    max_w = min(MAX_DESKLET_WIDTH, avail.right() - self.x() + 1) if avail else MAX_DESKLET_WIDTH
                    max_h = min(MAX_DESKLET_HEIGHT, avail.bottom() - self.y() + 1) if avail else MAX_DESKLET_HEIGHT
                    new_w = max(MIN_DESKLET_WIDTH, min(max_w, self._start_size.width() + delta.x()))
                    self._user_height = max(MIN_DESKLET_HEIGHT, min(max_h, self._start_size.height() + delta.y()))
                    self.setFixedWidth(new_w)
                    self._fit_height()
                    return True
                if event.type() == event.Type.MouseButtonRelease and self._drag_mode == "resize":
                    self._drag_mode = None
                    self._interacting = False
                    self._save_layout()
                    return True
        return super().eventFilter(obj, event)
