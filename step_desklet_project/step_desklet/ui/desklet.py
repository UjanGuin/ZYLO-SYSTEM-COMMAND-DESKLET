import sys
import os
import threading
import subprocess
from typing import Optional, Dict, List, Any
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QLabel, QTextEdit, 
    QFrame, QHBoxLayout, QMenu, QToolButton
)
from PyQt6.QtCore import Qt, QPoint, QTimer, QEvent
from PyQt6.QtGui import QAction
from ..core.worker import StepAIWorker
from ..core.config import update_instance

class StepDesklet(QWidget):
    def __init__(self, instance_id: str, config: Dict[str, Any], manager):
        super().__init__()
        self.instance_id = instance_id
        self.config = config
        self.manager = manager
        # Position lock only; desktop-layer lock is always enforced separately.
        self.is_locked = bool(config.get("is_locked", False))
        self.user_sized = bool(config.get("user_sized", False))
        self._is_closing = False
        self._internal_resize = False
        self._dragging = False
        self._resizing = False
        self.resize_start_global = QPoint()
        self.resize_start_size = self.size()
        self.resize_margin = 12
        self.drag_pos = QPoint()
        self.response_min_height = 18
        self.response_max_height = 420

        # Build Worker
        api_key = os.getenv("NVIDIA_API_KEY") or config.get("api_key") or "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"
        self.worker = StepAIWorker(
            api_key=api_key,
            base_url=config.get("base_url", "https://integrate.api.nvidia.com/v1"),
            model=config.get("model", "stepfun-ai/step-3.5-flash")
        )
        self.worker.signals.response_ready.connect(self.update_response)
        self.worker.signals.status_update.connect(self.update_status)
        self.worker.signals.error_occurred.connect(self.update_error)
        self.worker.signals.clearing_response.connect(self.clear_display)

        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.setInterval(140)
        self.geometry_save_timer.timeout.connect(self._persist_geometry)
        self.init_ui()
        self.apply_desklet_hints()

    def init_ui(self):
        # Desktop-only window: frameless and always below normal app windows.
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnBottomHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
            # Dock type stays visible during "Show Desktop" in Cinnamon/X11.
            self.setAttribute(Qt.WidgetAttribute.WA_X11NetWmWindowTypeDock, True)
        
        # Ensure it doesn't take focus unless clicked
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        self.setMinimumSize(230, 78)
        initial_w = max(self.minimumWidth(), int(self.config.get("w", 320)))
        initial_h = max(self.minimumHeight(), int(self.config.get("h", 110)))
        self.resize(initial_w, initial_h)
        self.move(self.config["x"], self.config["y"])

        # Main Container
        self.container = QFrame(self)
        self.container.setObjectName("DeskletBody")
        self.container.setStyleSheet("""
            #DeskletBody {
                background-color: rgba(10, 10, 15, 245);
                border: 2px solid #00d4ff;
                border-radius: 14px;
            }
        """)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.main_layout.addWidget(self.container)
        
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setContentsMargins(12, 9, 12, 8)
        self.content_layout.setSpacing(3)

        # Header Row
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel("ZYLO-SYSTEM-COMMAND-DESKLET")
        self.header.setStyleSheet("font-size: 18px; font-weight: 900; color: #00d4ff; background: transparent;")
        header_layout.addWidget(self.header)
        header_layout.addStretch(1)
        
        # Buttons Container
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(2)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        icon_btn_style = """
            QToolButton {
                color: #00d4ff;
                background: transparent;
                border: none;
                padding: 0px;
                font-size: 16px;
            }
            QToolButton:hover { color: #7eeeff; }
        """

        self.reset_btn = QToolButton(self)
        self.reset_btn.setText("↺")
        self.reset_btn.setStyleSheet(icon_btn_style)
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setToolTip("Reset")
        self.reset_btn.setFixedSize(18, 18)
        self.reset_btn.clicked.connect(self.reset_ui)
        btn_layout.addWidget(self.reset_btn)

        self.lock_btn = QToolButton(self)
        self.lock_btn.setText("🔒" if self.is_locked else "🔓")
        self.lock_btn.setStyleSheet(icon_btn_style)
        self.lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_btn.setToolTip("Position locked" if self.is_locked else "Position unlocked (drag to move)")
        self.lock_btn.setFixedSize(18, 18)
        self.lock_btn.clicked.connect(self.toggle_lock)
        btn_layout.addWidget(self.lock_btn)

        header_layout.addLayout(btn_layout)
        self.content_layout.addLayout(header_layout)

        # AI Response Area
        self.response_area = QTextEdit()
        self.response_area.setReadOnly(True)
        self.response_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.response_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.response_area.setFrameStyle(QFrame.Shape.NoFrame)
        self.response_area.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.response_area.document().setDocumentMargin(0)
        self.response_area.setStyleSheet("""
            QTextEdit {
                background: transparent;
                color: #f1faee;
                font-size: 13px;
                line-height: 1.2;
                margin: 0px;
                padding: 0px;
            }
        """)
        self.response_area.setFixedHeight(self.response_min_height)
        self.response_area.hide()
        self.content_layout.addWidget(self.response_area)

        # Status Label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: rgba(0, 212, 255, 180); font-size: 10px; background: transparent; margin: 0px; padding: 0px;")
        self.status_label.hide()
        self.content_layout.addWidget(self.status_label)

        # Input Box
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Command Guardian...")
        self.input_box.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 10);
                border: 1px solid rgba(0, 212, 255, 120);
                border-radius: 10px;
                padding: 7px 11px;
                color: white;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #00d4ff;
                background-color: rgba(255, 255, 255, 15);
            }
        """)
        self.input_box.returnPressed.connect(self.send_message)
        self.content_layout.addWidget(self.input_box)

        # Set Context Menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.reset_ui()
        QTimer.singleShot(0, self.apply_desklet_hints)
        QTimer.singleShot(200, self.apply_desklet_hints)
        QTimer.singleShot(1200, self.apply_desklet_hints)
        self.hint_refresh_timer = QTimer(self)
        self.hint_refresh_timer.setInterval(1500)
        self.hint_refresh_timer.timeout.connect(self.apply_desklet_hints)
        self.hint_refresh_timer.start()

    def apply_desklet_hints(self):
        """Keep desklet visible on desktop-show while staying below normal app windows."""
        if sys.platform != "linux":
            return
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return

        try:
            window_id = f"0x{int(self.winId()):x}"
        except Exception:
            return

        commands = [
            [
                "xprop", "-id", window_id,
                "-f", "_NET_WM_WINDOW_TYPE", "32a",
                "-set", "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DOCK"
            ],
            [
                "xprop", "-id", window_id,
                "-f", "_NET_WM_STATE", "32a",
                "-set", "_NET_WM_STATE",
                "_NET_WM_STATE_BELOW,_NET_WM_STATE_STICKY,_NET_WM_STATE_SKIP_TASKBAR,_NET_WM_STATE_SKIP_PAGER"
            ],
            ["wmctrl", "-i", "-r", window_id, "-b", "add,below,sticky,skip_taskbar,skip_pager"],
            ["wmctrl", "-i", "-r", window_id, "-b", "remove,hidden"],
        ]

        for command in commands:
            try:
                subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                continue

    def show_context_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #1a1a1a; color: white; border: 1px solid #00d4ff; }
            QMenu::item:selected { background-color: #00d4ff; color: black; }
        """)
        new_act = QAction("Add New Desklet", self)
        new_act.triggered.connect(self.manager.create_desklet)
        lock_act = QAction("Unlock Position" if self.is_locked else "Lock Position", self)
        lock_act.triggered.connect(self.toggle_lock)
        rem_act = QAction("Remove This Desklet", self)
        rem_act.triggered.connect(lambda: self.manager.remove_desklet(self.instance_id))
        exit_act = QAction("Exit All", self)
        exit_act.triggered.connect(sys.exit)
        menu.addAction(new_act); menu.addAction(lock_act); menu.addAction(rem_act); menu.addSeparator(); menu.addAction(exit_act)
        menu.exec(self.mapToGlobal(pos))

    def reset_ui(self):
        self.response_area.clear()
        self.response_area.hide()
        self.status_label.hide()
        self.input_box.setEnabled(True)
        self.input_box.setFocus()
        self._adjust_after_content_change(allow_shrink=True)

    def toggle_lock(self, event=None):
        self.is_locked = not self.is_locked
        self.lock_btn.setText("🔒" if self.is_locked else "🔓")
        self.lock_btn.setToolTip("Position locked" if self.is_locked else "Position unlocked (drag to move)")
        if self.is_locked:
            self._dragging = False
            self._resizing = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        try:
            update_instance(self.instance_id, {"is_locked": self.is_locked, "user_sized": self.user_sized})
        except Exception:
            pass

    def mousePressEvent(self, event):
        if not self.is_locked and event.button() == Qt.MouseButton.LeftButton:
            local_pos = event.position().toPoint()
            if self._is_in_resize_zone(local_pos):
                self._resizing = True
                self.resize_start_global = event.globalPosition().toPoint()
                self.resize_start_size = self.size()
                event.accept()
                return

            self._dragging = True
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_locked:
            return

        local_pos = event.position().toPoint()
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._is_in_resize_zone(local_pos) else Qt.CursorShape.ArrowCursor)
            return

        if self._resizing:
            delta = event.globalPosition().toPoint() - self.resize_start_global
            new_w = max(self.minimumWidth(), self.resize_start_size.width() + delta.x())
            new_h = max(self.minimumHeight(), self.resize_start_size.height() + delta.y())
            self._internal_resize = True
            self.resize(new_w, new_h)
            self._internal_resize = False
            self.user_sized = True
            self.geometry_save_timer.start()
            event.accept()
            return

        if self._dragging:
            new_pos = event.globalPosition().toPoint() - self.drag_pos
            self.move(new_pos)
            self.geometry_save_timer.start()
            event.accept()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        had_interaction = self._dragging or self._resizing
        self._dragging = False
        self._resizing = False
        if not self.is_locked:
            local_pos = self.mapFromGlobal(event.globalPosition().toPoint())
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._is_in_resize_zone(local_pos) else Qt.CursorShape.ArrowCursor)
        if had_interaction:
            self.geometry_save_timer.stop()
            self._persist_geometry()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def send_message(self):
        text = self.input_box.text().strip()
        if not text: return
        self.input_box.clear()
        self.input_box.setEnabled(False)
        threading.Thread(target=self.worker.process_request, args=(text,), daemon=True).start()

    def clear_display(self):
        self.response_area.clear()
        self.response_area.setFixedHeight(self.response_min_height)
        self.response_area.hide()
        self.status_label.setText("Analyzing...")
        self.status_label.show()
        self._adjust_after_content_change(allow_shrink=True)

    def update_status(self, status: str):
        if status:
            self.status_label.setText(status)
            self.status_label.show()
        else:
            self.status_label.hide()
        self._adjust_after_content_change(allow_shrink=True)

    def update_response(self, response: str):
        self.response_area.setMarkdown(response)
        self.response_area.show()
        self.status_label.hide()
        self.input_box.setEnabled(True)
        self.input_box.setFocus()
        QTimer.singleShot(0, self._finalize_output_layout)

    def update_error(self, error: str):
        self.response_area.setHtml(f"<font color='#ff4d6d'>Error: {error}</font>")
        self.response_area.show()
        self.status_label.hide()
        self.input_box.setEnabled(True)
        QTimer.singleShot(0, self._finalize_output_layout)

    def _finalize_output_layout(self):
        self._update_response_height()
        self._adjust_after_content_change(allow_shrink=False)

    def _adjust_after_content_change(self, allow_shrink: bool):
        """Keep current width, but always grow height to fit dynamic response/status content."""
        self.main_layout.activate()
        self.content_layout.activate()

        needed_h = max(self.minimumHeight(), int(self.layout().sizeHint().height()))
        target_h = needed_h if allow_shrink else max(self.height(), needed_h)
        if target_h != self.height():
            self._internal_resize = True
            self.resize(self.width(), target_h)
            self._internal_resize = False
        self.geometry_save_timer.start()

    def _update_response_height(self):
        if not self.response_area.isVisible():
            return

        viewport_width = max(40, self.response_area.viewport().width())
        document = self.response_area.document()
        document.setTextWidth(viewport_width)
        doc_height = document.documentLayout().documentSize().height()

        target_height = int(doc_height + 4)
        target_height = max(self.response_min_height, min(self.response_max_height, target_height))
        if self.response_area.height() != target_height:
            self.response_area.setFixedHeight(target_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_response_height()
        self.geometry_save_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.apply_desklet_hints)
        QTimer.singleShot(200, self.apply_desklet_hints)

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self._is_closing:
            QTimer.singleShot(50, self._ensure_visible)

    def changeEvent(self, event):
        super().changeEvent(event)
        if self._is_closing:
            return
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                QTimer.singleShot(0, self._ensure_visible)

    def closeEvent(self, event):
        self.geometry_save_timer.stop()
        self._persist_geometry()
        self._is_closing = True
        super().closeEvent(event)

    def _ensure_visible(self):
        if self._is_closing:
            return
        if self.windowState() & Qt.WindowState.WindowMinimized:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        if not self.isVisible():
            self.show()
        self.apply_desklet_hints()

    def _persist_geometry(self):
        try:
            update_instance(
                self.instance_id,
                {
                    "x": int(self.x()),
                    "y": int(self.y()),
                    "w": int(self.width()),
                    "h": int(self.height()),
                    "is_locked": self.is_locked,
                    "user_sized": self.user_sized,
                },
            )
        except Exception:
            pass

    def _is_in_resize_zone(self, pos: QPoint) -> bool:
        return (
            pos.x() >= self.width() - self.resize_margin
            and pos.y() >= self.height() - self.resize_margin
        )
