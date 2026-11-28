"""The themed backdrop and motion shared by the Chats transcript views."""

from functools import lru_cache
from math import ceil, cos, pi, sin, tau
from random import Random
from time import monotonic

from PyQt6.QtCore import QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QRadialGradient
from PyQt6.QtWidgets import QStyle, QWidget


REPLY_REVEAL_MS = 320
SEND_TRANSITION_MS = 300


@lru_cache(maxsize=1)
def _dither_brush():
    tile = QImage(64, 64, QImage.Format.Format_RGBX64)
    rng = Random(0)
    for y in range(0, tile.height(), 2):
        for x in range(0, tile.width(), 2):
            # Distribute the two levels of noise evenly within each 2x2 group.
            values = [(step + rng.random()) / (2 * 255) for step in range(4)]
            rng.shuffle(values)
            for index, gray in enumerate(values):
                tile.setPixelColor(x + index % 2, y + index // 2, QColor.fromRgbF(gray, gray, gray))
    return QBrush(tile)


def _dither_centered_color(color):
    return QColor.fromRgbF(*(max(0, value - 1 / 255) for value in color.getRgbF()[:3]))


def _render_backdrop(size, colors, time, intensity):
    # Preserve fractional color levels while blending and scaling the glow.
    glow = QImage(320, 240, QImage.Format.Format_RGBX64)
    glow.fill(_dither_centered_color(QColor("#14161f")))
    phase = time * tau / 20
    with QPainter(glow) as painter:
        painter.scale(glow.width(), glow.height())
        for index, (color, x, y, strength) in enumerate(zip(
            colors, (0.27, 0.78, 0.54), (0.30, 0.52, 0.91), (31, 28, 36),
        )):
            angle = phase + index * tau / 3
            center = QPointF(x + 0.16 * cos(angle), y + 0.13 * sin(angle))
            gradient = QRadialGradient(center, 0.56)
            tint = _dither_centered_color(color)
            peak = (strength + 34 * intensity) / 255
            for step in range(33):
                stop = step / 32
                tint.setAlphaF(peak * (1 - stop * stop) ** 3)
                gradient.setColorAt(stop, tint)
            painter.fillRect(QRectF(0, 0, 1, 1), gradient)

    scaled = glow.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    # Apply stationary dither in device pixels before rounding to RGB32.
    with QPainter(scaled) as painter:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        painter.fillRect(scaled.rect(), _dither_brush())
    return scaled.convertToFormat(QImage.Format.Format_RGB32)


class BackdropRender(QRunnable):
    def __init__(self, ready, revision, size, colors, time, intensity):
        super().__init__()
        self.ready = ready
        self.revision = revision
        self.size = size
        self.colors = colors
        self.time = time
        self.intensity = intensity

    def run(self):
        image = _render_backdrop(self.size, self.colors, self.time, self.intensity)
        try:
            self.ready.emit(self.revision, image)
        except RuntimeError:
            # The window can be destroyed before the render finishes.
            pass


class ChatWorkspace(QWidget):
    frame_changed = pyqtSignal()
    _backdrop_ready = pyqtSignal(int, QImage)

    def __init__(self, colors):
        super().__init__()
        self.colors = colors
        self.motion_enabled = self.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration) > 0
        self.time = 0.0
        self._glow_time = 0.0
        self.backdrop_paused = False
        self._scroll_until = 0.0
        self.busy = False
        self.intensity = 0.0
        self._fade_progress = 0.0
        self._last_frame = monotonic()
        self._backdrop = QImage()
        self._backdrop_revision = 0
        self._render_pending = False
        self._backdrop_ready.connect(self._accept_backdrop, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self, interval=17, timerType=Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._animate)

    def set_running(self, running):
        if running and self.motion_enabled:
            if not self._timer.isActive():
                self._last_frame = monotonic()
                self._timer.start()
        else:
            self._timer.stop()

    def set_colors(self, colors):
        self.colors = colors
        self._invalidate_backdrop()

    def set_busy(self, busy):
        if self.busy == busy:
            return
        self.busy = busy
        if not self.motion_enabled:
            self._fade_progress = self.intensity = float(busy)
            self._invalidate_backdrop()

    def note_scroll(self):
        self._scroll_until = monotonic() + 0.28

    def _animate(self):
        now = monotonic()
        elapsed = min(now - self._last_frame, 0.1)
        self._last_frame = now
        self.time += elapsed
        self.frame_changed.emit()
        if self.backdrop_paused or now < self._scroll_until:
            return
        self._glow_time += elapsed
        if self.busy:
            self._fade_progress = min(1.0, self._fade_progress + elapsed / 0.48)
        else:
            self._fade_progress = max(0.0, self._fade_progress - elapsed / 0.60)
        progress = self._fade_progress
        self.intensity = progress * progress * (3 - 2 * progress)
        if not self._render_pending and not self._backdrop.isNull():
            self._render_pending = True
            QThreadPool.globalInstance().start(BackdropRender(
                self._backdrop_ready, self._backdrop_revision, self._backdrop.size(),
                self.colors, self._glow_time, self.intensity,
            ))

    def _accept_backdrop(self, revision, image):
        self._render_pending = False
        if (revision != self._backdrop_revision or not self._timer.isActive()
                or self.backdrop_paused or monotonic() < self._scroll_until):
            return
        self._backdrop = image
        self.update()

    def _invalidate_backdrop(self):
        self._backdrop_revision += 1
        self._backdrop = QImage()
        self.update()
        self.frame_changed.emit()

    def paint_background(self, painter, origin=QPointF()):
        ratio = self.devicePixelRatioF()
        size = QSize(ceil(self.width() * ratio), ceil(self.height() * ratio))
        if size.isEmpty():
            return
        if self._backdrop.size() != size:
            self._backdrop_revision += 1
            self._backdrop = _render_backdrop(size, self.colors, self._glow_time, self.intensity)
        self._backdrop.setDevicePixelRatio(ratio)
        transform = painter.deviceTransform()
        device_origin = transform.map(origin)
        aligned_origin = transform.inverted()[0].map(QPointF(round(device_origin.x()), round(device_origin.y())))
        painter.drawImage(aligned_origin, self._backdrop)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            self.paint_background(painter)

    def paint_thinking(self, painter, origin):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, color in enumerate(self.colors):
            phase = self.time * tau / 1.15 - index * pi / 3
            strength = (sin(phase) + 1) / 2 if self.motion_enabled else 0.64
            tint = QColor(color)
            tint.setAlphaF(0.38 + 0.62 * strength)
            painter.setBrush(tint)
            radius = 1.7 + 0.85 * strength
            painter.drawEllipse(origin + QPointF(index * 8, -2 * strength), radius, radius)
