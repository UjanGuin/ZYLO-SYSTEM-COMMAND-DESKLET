"""The full conversation window for the Windows desklet."""

import html
import json
import re
from collections import deque
from functools import lru_cache
from math import ceil
from pathlib import Path
from time import monotonic

from PyQt6.QtCore import (
    QEvent, QEasingCurve, QPoint, QPointF, QRectF, QSignalBlocker, QSize, Qt, QTimer, QUrl, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QCursor, QDesktopServices, QFont, QFontMetricsF, QKeySequence, QPainter, QPixmap, QShortcut, QTextCursor,
    QTextBlockFormat, QTextCharFormat, QTextDocument,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from step_desklet.core.config import DEFAULT_CHAT_BUBBLE_COLOR, PROVIDER_LABELS, enabled_providers
from step_desklet.core.assistant_context import WRITING_TASKS, writing_task_for_source
from step_desklet.win11.branding import zylo_app_icon
from step_desklet.win11.chat_motion import ChatWorkspace, REPLY_REVEAL_MS, SEND_TRANSITION_MS
from step_desklet.win11.chat_style import (
    CHAT_STYLE, FALLBACK_TRANSCRIPT_STYLE, TRANSCRIPT_STYLE, action_icon, action_image, icon_pixmap,
)
from step_desklet.win11.request_process import DeskletRequest

try:
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None


MATH_OR_CODE = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|\$\$[\s\S]+?\$\$"
    r"|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|(?<!\\)\$[^$\n]+?(?<!\\)\$"
)
TABLE_PIPE = re.compile(r"(?<!\\)\|")
TABLE_SEPARATOR = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?")
CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
QT_MARKDOWN_TABLE = re.compile(r'<table border="1"[^>]*>(\s*<tr\b.*?</tr>)(.*?)</table>', re.S)
BULLET = r" {0,3}(?:[-+*]|\d{1,9}[.)])[ \t]+"
LIST_ITEM = re.compile(BULLET)
BOLD_LABEL = re.compile(rf"^({BULLET})([^*\\`$]+?)(?<!\s)\*\*(?=[ \t]+[-\u2013\u2014][ \t]+)")
BOLD_HEADING = re.compile(r"^ {0,3}\*\*[^\s*\\`$][^*\\`$]*$")


def normalize_markdown(text):
    lines = text.split("\n")
    output = []
    fence = ""
    in_table = False
    for index, line in enumerate(lines):
        trimmed = line.lstrip(" ")
        has_pipe = len(line) - len(trimmed) < 4 and not trimmed.startswith(("\t", ">")) and TABLE_PIPE.search(line)
        marker = CODE_FENCE.match(line)
        if in_table and (not has_pipe or marker):
            in_table = False
            if line.strip():
                output.append("")
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
        elif not fence:
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            if not in_table and has_pipe and "|" in next_line and TABLE_SEPARATOR.fullmatch(next_line.strip()):
                if output and output[-1].strip():
                    output.append("")
                in_table = True
            # Providers sometimes leave a heading open and send list labels with only a closing delimiter.
            if line.count("**") == 1:
                if BOLD_HEADING.fullmatch(line) and LIST_ITEM.match(next_line):
                    line = line.rstrip() + "**"
                else:
                    line = BOLD_LABEL.sub(r"\1**\2**", line)
        output.append(line)
    return "\n".join(output)


def style_markdown_table(match):
    header = match[1].replace("<td", '<th style="background-color:#292d41;"').replace("</td>", "</th>")
    return (
        '<table border="1" cellspacing="0" cellpadding="6" '
        'style="border:1px solid #414459;border-collapse:collapse;margin-top:6px;margin-bottom:6px;">'
        f'<thead>{header}</thead><tbody>{match[2]}</tbody></table>'
    )


def markdown_html(text, *, math=False):
    equations = {}
    highlighted = {}
    tick = chr(96) * 3

    def highlight_fence(match):
        try:
            from pygments import highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import TextLexer, get_lexer_by_name
            from pygments.util import ClassNotFound
        except ImportError:
            return match[0]
        try:
            lexer = get_lexer_by_name(match[1]) if match[1] else TextLexer()
        except ClassNotFound:
            lexer = TextLexer()
        token = f"ZYLOCODE{len(highlighted)}X"
        highlighted[token] = highlight(match[2], lexer, HtmlFormatter(noclasses=True, style="native"))
        return "\n\n" + token + "\n\n"

    text = normalize_markdown(text)
    text = re.sub(tick + r"([-\w]*)[^\n]*\n([\s\S]*?)" + tick, highlight_fence, text)
    if math:
        def preserve(match):
            raw = match[0]
            if raw.startswith(chr(96)):
                return raw
            token = f"ZYLOEQ{len(equations)}X"
            equations[token] = html.escape(raw)
            return token
        text = MATH_OR_CODE.sub(preserve, text)
    document = QTextDocument()
    document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub
                         | QTextDocument.MarkdownFeature.MarkdownNoHTML)
    result = document.toHtml()
    body = re.search(r"<body\b[^>]*>([\s\S]*?)</body>", result)
    result = body[1] if body else result
    result = QT_MARKDOWN_TABLE.sub(style_markdown_table, result)
    result = re.sub(r"<img\b[^>]*>", "", result, flags=re.I)
    result = re.sub(r'href="(?!https?://)[^"]*"', 'href=""', result, flags=re.I)
    for token, equation in equations.items():
        result = result.replace(token, equation)
    for token, code in highlighted.items():
        result = re.sub(r"<p\b[^>]*>" + token + r"</p>", lambda _: code, result)
    return result


cached_markdown_html = lru_cache(maxsize=96)(markdown_html)


if QWebEngineView is not None:
    class TranscriptPage(QWebEnginePage):
        action = pyqtSignal(QUrl)

        def acceptNavigationRequest(self, url, kind, main_frame):
            if kind == self.NavigationType.NavigationTypeLinkClicked:
                self.action.emit(url)
                return False
            return url.scheme() in ("data", "file", "about")


class ChatTranscript(QTextBrowser):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bubble_color = QColor(DEFAULT_CHAT_BUBBLE_COLOR)
        self.live_turn = False
        self.prompt_started = None
        self.reply_started = None
        self._bubble_blocks = []
        self._action_rows = []
        self.viewport().setMouseTracking(True)
        self._thinking_block = self._reply_block = None
        self._scroll = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self._scroll.valueChanged.connect(self.verticalScrollBar().setValue)

    def setHtml(self, markup):
        self._bubble_blocks = []
        self._action_rows = []
        self._thinking_block = self._reply_block = None
        super().setHtml(markup)
        self._index_blocks(self.document().begin())
        self._align_bubble_text()
        self._pad_for_latest()

    def _index_blocks(self, block):
        while block.isValid():
            first = block.begin().fragment()
            if first.isValid():
                anchors = first.charFormat().anchorNames()
                if "zylo-user-message" in anchors:
                    self._bubble_blocks.append(block)
                elif "lini-thinking" in anchors:
                    self._thinking_block = block
                elif "lini-reply" in anchors:
                    self._reply_block = block
                if first.charFormat().anchorHref().startswith("https://zylo.invalid/"):
                    self._action_rows.append(block)
            block = block.next()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._align_bubble_text()
        self._pad_for_latest()

    def update_reply_tail(self, markup, *, finished):
        if self._reply_block is None or not self._reply_block.isValid():
            return False
        cursor = QTextCursor(self._reply_block)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        cursor.insertHtml(
            f'<style>{FALLBACK_TRANSCRIPT_STYLE}</style>'
            f'<body style="color:#eef0f7">{markup}</body>'
        )
        cursor.endEditBlock()
        if finished:
            self._index_blocks(self._reply_block.next())
        self._pad_for_latest()
        return True

    def wheelEvent(self, event):
        self.workspace.note_scroll()
        bar = self.verticalScrollBar()
        angle = event.angleDelta().y()
        if not self.workspace.motion_enabled or not angle or event.pixelDelta().y() or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._scroll.stop()
            super().wheelEvent(event)
            return
        step = QApplication.wheelScrollLines() * max(12, self.fontMetrics().lineSpacing())
        current = self._scroll.endValue() if self.is_scrolling else bar.value()
        self._animate_scroll(current - round(angle * step / 120))
        event.accept()

    @property
    def is_scrolling(self):
        return self._scroll.state() == QVariantAnimation.State.Running

    def keyPressEvent(self, event):
        self._scroll.stop()
        if event.key() in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Up, Qt.Key.Key_Down,
                           Qt.Key.Key_Home, Qt.Key.Key_End):
            self.workspace.note_scroll()
        super().keyPressEvent(event)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        # Paint the transparent viewport over the stationary glow after a scroll.
        self.viewport().update()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.viewport().update()

    def viewportEvent(self, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self.viewport().update()
        return super().viewportEvent(event)

    def _latest_top(self):
        if not self._bubble_blocks:
            return 0
        bounds = self.document().documentLayout().blockBoundingRect(self._bubble_blocks[-1])
        return max(0, int(bounds.top() - 16))

    def _pad_for_latest(self):
        doc = self.document()
        root = doc.rootFrame()
        frame = root.frameFormat()
        old_margin = frame.bottomMargin()
        margin = doc.documentMargin()
        if self.live_turn and self._bubble_blocks:
            extra = ceil(self._latest_top() + self.viewport().height() - doc.size().height() + old_margin)
            margin = max(margin, extra)
        if old_margin != margin:
            frame.setBottomMargin(margin)
            root.setFrameFormat(frame)

    def scroll_to_latest(self):
        self._animate_scroll(self._latest_top())

    def _animate_scroll(self, target):
        bar = self.verticalScrollBar()
        top = max(bar.minimum(), min(bar.maximum(), target))
        if self.is_scrolling and self._scroll.endValue() == top:
            return
        self._scroll.stop()
        if not self.workspace.motion_enabled or not self.isVisible() or top == bar.value():
            bar.setValue(top)
            return
        with QSignalBlocker(self._scroll):
            self._scroll.setStartValue(bar.value())
            self._scroll.setEndValue(top)
            self._scroll.setCurrentTime(0)
        self._scroll.start()

    def _align_bubble_text(self):
        if not self._bubble_blocks:
            return
        document = self.document()
        available = self.viewport().width() - 2 * document.documentMargin()
        # Reflow the transcript once after aligning the bubbles so intervening replies are laid out too.
        document.setLayoutEnabled(False)
        try:
            for block in self._bubble_blocks:
                cursor = QTextCursor(block)
                fmt = cursor.blockFormat()
                font = block.begin().fragment().charFormat().font()
                metrics = QFontMetricsF(font)
                longest_line = max(map(metrics.horizontalAdvance, block.text().splitlines()), default=0)
                left = max(50, available - fmt.rightMargin() - longest_line)
                if fmt.alignment() != Qt.AlignmentFlag.AlignLeft or abs(fmt.leftMargin() - left) >= 1:
                    fmt.setAlignment(Qt.AlignmentFlag.AlignLeft)
                    fmt.setLeftMargin(left)
                    cursor.setBlockFormat(fmt)
        finally:
            document.setLayoutEnabled(True)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bubble_color)
        offset_y = self.verticalScrollBar().value()
        offset_x = self.horizontalScrollBar().value()
        layout = self.document().documentLayout()
        prompt_rect = QRectF()
        for block in self._bubble_blocks:
            bounds = layout.blockBoundingRect(block)
            top = bounds.top() - offset_y
            if top + bounds.height() < event.rect().top() - 10 or top > event.rect().bottom() + 10:
                continue
            lines = block.layout()
            rect = QRectF()
            for index in range(lines.lineCount()):
                rect = rect.united(lines.lineAt(index).naturalTextRect())
            if not rect.isEmpty():
                rect.translate(bounds.x() - offset_x, top)
                rect.adjust(-14, -8, 14, 8)
                painter.drawRoundedRect(rect, 17, 17)
                if self.prompt_started is not None and block == self._bubble_blocks[-1]:
                    prompt_rect = rect.adjusted(-2, -2, 2, 2)
        painter.end()
        super().paintEvent(event)
        self._paint_actions()

        if self._thinking_block or (self._reply_block and self.reply_started is not None) or not prompt_rect.isEmpty():
            self._paint_activity(offset_x, offset_y, prompt_rect)

    def _update_motion(self):
        if self.prompt_started is not None:
            if (monotonic() - self.prompt_started) * 1000 >= SEND_TRANSITION_MS:
                self.prompt_started = None
            self.viewport().update()
        if self._thinking_block:
            self.viewport().update()
        elif self._reply_block and self.reply_started is not None:
            if (monotonic() - self.reply_started) * 1000 >= REPLY_REVEAL_MS:
                self.reply_started = None
            self.viewport().update()

    def _paint_actions(self):
        point = self.viewport().mapFromGlobal(QCursor.pos())
        if not self.viewport().rect().contains(point):
            return
        with QPainter(self.viewport()) as painter:
            for block in self._action_rows:
                cursor = QTextCursor(block)
                row = QRectF(self.cursorRect(cursor))
                if not row.adjusted(-30, -26, 130, 13).contains(QPointF(point)):
                    continue
                fragments = block.begin()
                while not fragments.atEnd():
                    fragment = fragments.fragment()
                    path = fragment.charFormat().anchorHref()
                    if fragment.charFormat().isImageFormat() and path.startswith("https://zylo.invalid/"):
                        cursor.setPosition(fragment.position())
                        rect = self.cursorRect(cursor)
                        icon = icon_pixmap("edit" if "/edit/" in path else "copy")
                        painter.drawPixmap(rect.x(), rect.y() + (rect.height() - 18) // 2, icon)
                    fragments += 1

    def _paint_reveal(self, painter, rect, started, duration):
        progress = min(1.0, (monotonic() - started) * 1000 / duration)
        if progress < 1 and not rect.isEmpty():
            painter.save()
            painter.setClipRect(rect)
            painter.setOpacity((1 - progress) ** 2)
            origin = self.viewport().mapTo(self.workspace, QPoint())
            self.workspace.paint_background(painter, -QPointF(origin))
            painter.restore()

    def _paint_activity(self, offset_x, offset_y, prompt_rect):
        layout = self.document().documentLayout()
        with QPainter(self.viewport()) as painter:
            if self.prompt_started is not None:
                self._paint_reveal(painter, prompt_rect, self.prompt_started, SEND_TRANSITION_MS)
            if self._thinking_block:
                bounds = layout.blockBoundingRect(self._thinking_block)
                line = self._thinking_block.layout().lineAt(0)
                if line.isValid():
                    text = line.naturalTextRect()
                    painter.setPen(Qt.PenStyle.NoPen)
                    self.workspace.paint_thinking(painter, QPointF(
                        bounds.left() + text.left() - offset_x - 24,
                        bounds.top() + text.center().y() - offset_y + 1,
                    ))
            if self._reply_block and self.reply_started is not None:
                top = max(0, layout.blockBoundingRect(self._reply_block).top() - offset_y)
                rect = QRectF(0, top, self.viewport().width(), max(0, self.viewport().height() - top))
                self._paint_reveal(painter, rect, self.reply_started, REPLY_REVEAL_MS)

class ChatComposer(QPlainTextEdit):
    send_requested = pyqtSignal()
    focus_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self._preedit = ""
        self.setTabChangesFocus(True)

    def inputMethodEvent(self, event):
        self._preedit = event.preeditString()
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not modifiers and not self._preedit):
            if not event.isAutoRepeat():
                self.send_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._preedit = ""
        self.focus_changed.emit(False)


class SidebarPane(QWidget):
    """Clip the sidebar while its full-width content slides into view."""

    INSET = 12

    def __init__(self, content):
        super().__init__()
        self.content = content
        content.setParent(self)
        self.slide_width = None
        self.restore_size_limits()

    def restore_size_limits(self):
        self.setMinimumWidth(self.content.minimumWidth() + 2 * self.INSET)
        self.setMaximumWidth(self.content.maximumWidth() + 2 * self.INSET)

    def position_content(self):
        width = self.slide_width or self.width()
        self.content.setGeometry(
            self.width() - width + self.INSET, self.INSET,
            max(0, width - 2 * self.INSET), max(0, self.height() - 2 * self.INSET),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_content()


class ChatsWindow(QMainWindow):
    def __init__(self, desklet, voice=None):
        super().__init__()
        self.desklet = desklet
        self.store = desklet.chat_store
        self._chat_id = self.store.active()
        self.setWindowTitle("liNi Chats")
        self.setWindowIcon(zylo_app_icon())
        self.resize(1160, 780)
        self.setMinimumSize(660, 450)
        self.setFont(QFont("Segoe UI", 10))
        self.assets_dir = Path(__file__).with_name("assets")
        self.setStyleSheet(CHAT_STYLE.replace("@CHEVRON@", (self.assets_dir / "chat-chevron.svg").as_posix()))
        self._partial = ""
        self._tokens = deque()
        self._draining_tokens = False
        self._title_candidate = None
        self._token_timer = QTimer(self, interval=22, timerType=Qt.TimerType.PreciseTimer)
        self._token_timer.timeout.connect(self._reveal_tokens)
        self._summary = ""
        self._actions = {}
        self._editing = None
        self._editing_task = None
        self._page_started = False
        self._page_ready = False
        self._pending_markup = ""
        self._web_revision = 0
        self._web_view = None
        self._web_timer = QTimer(self)
        self._web_timer.setSingleShot(True)
        self._web_timer.timeout.connect(self._use_native_transcript)
        self._rendered_chat = None
        self._live_message = None
        self._prompt_started = None
        self._reply_started = None
        self._jump_to_latest = False
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(65)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)

        self.workspace = ChatWorkspace(desklet.card.colors)
        self.setCentralWidget(self.workspace)
        layout = QVBoxLayout(self.workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        container = self._columns = QSplitter()
        container.setChildrenCollapsible(False)
        container.setHandleWidth(1)
        layout.addWidget(container)
        self.sidebar = self._build_sidebar()
        self._sidebar_pane = SidebarPane(self.sidebar)
        container.addWidget(self._sidebar_pane)
        self._sidebar_width = 238 + 2 * self._sidebar_pane.INSET
        self._sidebar_animation = QVariantAnimation(
            self, duration=250, easingCurve=QEasingCurve(QEasingCurve.Type.InOutCubic),
        )
        self._sidebar_animation.valueChanged.connect(self._sidebar_pane.setFixedWidth)
        self._sidebar_animation.finished.connect(self._finish_sidebar_slide)

        conversation = QWidget()
        right = QVBoxLayout(conversation)
        right.setContentsMargins(0, 0, 0, 16)
        right.setSpacing(0)
        right.addWidget(self._build_header())
        self.transcript_wrap = self._build_transcript()
        right.addWidget(self.transcript_wrap, 1)
        self.welcome_top = QWidget()
        right.addWidget(self.welcome_top, 1)
        self.welcome = self._build_welcome()
        right.addWidget(self.welcome)
        right.addWidget(self._build_composer(voice))
        self.welcome_bottom = QWidget()
        right.addWidget(self.welcome_bottom, 1)
        self._send_ghosts = None
        self._send_animation = QVariantAnimation(
            self, startValue=0.0, endValue=1.0, duration=SEND_TRANSITION_MS,
            easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic),
        )
        self._send_animation.valueChanged.connect(self._move_sent_composer)
        self._send_animation.finished.connect(self._finish_send_animation)
        container.addWidget(conversation)
        container.setSizes([self._sidebar_width, 922])
        container.setStretchFactor(0, 0)
        container.setStretchFactor(1, 1)

        refresh = QShortcut(QKeySequence.StandardKey.Refresh, self)
        refresh.activated.connect(self.refresh)
        desklet.chat_changed.connect(self.refresh)
        desklet.chat_status.connect(self._status)
        desklet.chat_preview.connect(self._preview)
        desklet.chat_finished.connect(self._finished)
        desklet.provider_changed.connect(lambda _: self.refresh_providers())
        desklet.theme_changed.connect(self._sync_theme)
        self.refresh_providers()
        self.refresh()

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("History")
        sidebar.setMinimumWidth(185)
        sidebar.setMaximumWidth(292)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(15, 19, 15, 12)
        left.setSpacing(12)
        brand = QHBoxLayout()
        brand.setSpacing(9)
        mark = QLabel()
        mark.setPixmap(zylo_app_icon().pixmap(28, 28))
        brand.addWidget(mark)
        title = QLabel("liNi Chats")
        title.setObjectName("Brand")
        brand.addWidget(title, 1)
        left.addLayout(brand)
        self.new = QPushButton(action_icon("new"), "New chat")
        self.new.setObjectName("NewChat")
        self.new.clicked.connect(self._new)
        left.addWidget(self.new)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search chats")
        self.search.setAccessibleName("Search chats")
        self.search.textChanged.connect(self._refresh_list)
        left.addWidget(self.search)
        history = QLabel("Conversations")
        history.setObjectName("HistoryLabel")
        left.addWidget(history)
        self.items = QListWidget()
        self.items.setAccessibleName("Conversations")
        self.items.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.itemActivated.connect(self._activate_item)
        self.items.itemClicked.connect(self._activate_item)
        left.addWidget(self.items, 1)
        return sidebar

    def _activate_item(self, item):
        self._select(item.data(Qt.ItemDataRole.UserRole))

    def _build_header(self):
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 7, 12, 7)
        row.setSpacing(7)
        self.sidebar_toggle = self._button("sidebar", "Collapse conversations")
        self.sidebar_toggle.setCheckable(True)
        self.sidebar_toggle.setChecked(True)
        self.sidebar_toggle.toggled.connect(self._toggle_sidebar)
        row.addWidget(self.sidebar_toggle)
        self.header_new = self._button("compose", "New chat")
        self.header_new.clicked.connect(self._new)
        row.addWidget(self.header_new)
        self.header = QLabel("New chat")
        self.header.setObjectName("ChatTitle")
        self.header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.header.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.header, 1)
        self.chat_menu = self._button("more", "Conversation options")
        menu = QMenu(self.chat_menu)
        self.rename = menu.addAction("Rename chat", self._rename)
        self.delete = menu.addAction("Delete chat", self._delete)
        self.chat_menu.setMenu(menu)
        self.chat_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addWidget(self.chat_menu)
        return header

    def _toggle_sidebar(self, expanded):
        label = "Collapse conversations" if expanded else "Expand conversations"
        self.sidebar_toggle.setToolTip(label)
        self.sidebar_toggle.setAccessibleName(label)
        pane = self._sidebar_pane
        current = 0 if pane.isHidden() else pane.width()
        if not expanded and self._sidebar_animation.state() != QVariantAnimation.State.Running:
            self._sidebar_width = max(pane.minimumWidth(), current)
        self._sidebar_animation.stop()
        if not self.workspace.motion_enabled or not self.isVisible():
            self._finish_sidebar_slide()
            return
        # Reuse the dithered image through the slide to leave time for layout.
        self.workspace.backdrop_paused = True
        pane.slide_width = self._sidebar_width
        pane.setFixedWidth(current)
        pane.show()
        pane.position_content()
        with QSignalBlocker(self._sidebar_animation):
            self._sidebar_animation.setStartValue(current)
            self._sidebar_animation.setEndValue(self._sidebar_width if expanded else 0)
            self._sidebar_animation.setCurrentTime(0)
        self._sidebar_animation.start()

    def _finish_sidebar_slide(self):
        self.workspace.backdrop_paused = False
        pane = self._sidebar_pane
        expanded = self.sidebar_toggle.isChecked()
        pane.slide_width = None
        if expanded:
            pane.restore_size_limits()
            pane.show()
            self._columns.setSizes([self._sidebar_width, max(0, self._columns.width() - self._sidebar_width)])
            pane.position_content()
        else:
            pane.hide()

    def _build_transcript(self):
        self.has_math = QWebEngineView is not None and (self.assets_dir / "tex-svg.js").is_file()
        self.viewer = ChatTranscript(self.workspace)
        self.viewer.viewport().setAutoFillBackground(False)
        self.workspace.frame_changed.connect(self.viewer._update_motion)
        self.viewer.setMaximumWidth(825)
        self.viewer.setViewportMargins(18, 0, 18, 0)
        self.viewer.document().setDocumentMargin(10)
        self.viewer.setOpenLinks(False)
        self.viewer.anchorClicked.connect(self._link)
        self.viewer.setAccessibleName("Conversation messages")
        self._native_transcript = self._center(self.viewer)
        self._transcript_stack = QStackedWidget()
        self._transcript_stack.addWidget(self._native_transcript)
        return self._transcript_stack

    def _start_web_view(self):
        view = QWebEngineView(self._transcript_stack)
        page = TranscriptPage(view)
        page.scrollPositionChanged.connect(lambda _: self.workspace.note_scroll())
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.setBackgroundColor(QColor(Qt.GlobalColor.transparent))
        page.action.connect(self._link)
        view.setPage(page)
        view.setAccessibleName("Conversation messages with math")
        view.loadFinished.connect(self._web_ready)
        page.renderProcessTerminated.connect(self._use_native_transcript)
        self._web_view = view
        self._transcript_stack.addWidget(view)
        self._page_started = True
        self._web_timer.start(5000)
        delimiters = json.dumps([["$", "$"], [r"\(", r"\)"]])
        shell = (
            "<meta charset=utf-8><meta http-equiv='Content-Security-Policy' "
            "content=\"default-src 'none'; script-src 'self' file: 'unsafe-inline'; style-src 'unsafe-inline'; "
            "font-src file:; img-src data:;\">"
            f"<script>window.MathJax={{tex:{{inlineMath:{delimiters}}},"
            "svg:{fontCache:'none'},startup:{typeset:false}};</script>"
            "<script>let wheelTarget=null;"
            "addEventListener('wheel',event=>{"
            "if(event.ctrlKey||event.shiftKey||Math.abs(event.deltaX)>Math.abs(event.deltaY)||"
            "document.documentElement.classList.contains('reduce-motion')||"
            "matchMedia('(prefers-reduced-motion: reduce)').matches)return;"
            "if(event.deltaMode===0&&Math.abs(event.deltaY)<40){wheelTarget=null;return;}"
            "const factor=event.deltaMode===1?20:event.deltaMode===2?innerHeight:1;"
            "const max=Math.max(0,document.documentElement.scrollHeight-innerHeight);"
            "wheelTarget=Math.min(max,Math.max(0,(wheelTarget??scrollY)+factor*event.deltaY));"
            "event.preventDefault();scrollTo({top:wheelTarget,behavior:'smooth'});"
            "},{passive:false});"
            "addEventListener('scrollend',()=>{wheelTarget=null;});"
            "addEventListener('keydown',()=>{wheelTarget=null;});</script>"
            '<script defer src="tex-svg.js"></script><main id="transcript"></main>'
        )
        view.setHtml(f"<style>{TRANSCRIPT_STYLE}</style>{shell}", QUrl.fromLocalFile(str(self.assets_dir) + "/"))

    @staticmethod
    def _build_welcome():
        welcome = QWidget()
        column = QVBoxLayout(welcome)
        column.setContentsMargins(16, 0, 16, 20)
        column.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(zylo_app_icon().pixmap(48, 48))
        column.addWidget(mark, alignment=Qt.AlignmentFlag.AlignHCenter)
        title = QLabel("Start a conversation")
        title.setObjectName("Welcome")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(title)
        return welcome

    @staticmethod
    def _center(widget):
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch()
        row.addWidget(widget, 1000)
        row.addStretch()
        return wrapper

    def _build_composer(self, voice):
        activity = QWidget()
        activity.setMaximumWidth(780)
        column = QVBoxLayout(activity)
        column.setContentsMargins(16, 3, 16, 0)
        column.setSpacing(5)
        self.thinking = QPushButton(action_icon("chevron"), "Activity")
        self.thinking.setCheckable(True)
        self.thinking.toggled.connect(self._show_thinking)
        self.thinking.hide()
        column.addWidget(self.thinking, 0, Qt.AlignmentFlag.AlignLeft)
        self.progress = QPlainTextEdit()
        self.progress.setReadOnly(True)
        self.progress.setFixedHeight(65)
        self.progress.setAccessibleName("Thinking details")
        self.progress.hide()
        column.addWidget(self.progress)

        self.composer_frame = QFrame()
        self.composer_frame.setObjectName("Composer")
        frame = QVBoxLayout(self.composer_frame)
        frame.setContentsMargins(11, 5, 8, 7)
        frame.setSpacing(2)
        self.edit_bar = QWidget()
        edit_row = QHBoxLayout(self.edit_bar)
        edit_row.setContentsMargins(5, 0, 0, 0)
        edit_label = QLabel("Editing message")
        edit_label.setObjectName("Activity")
        edit_row.addWidget(edit_label, 1)
        cancel_edit = self._button("cancel", "Cancel editing")
        cancel_edit.clicked.connect(self._cancel_edit)
        edit_row.addWidget(cancel_edit)
        self.edit_bar.hide()
        frame.addWidget(self.edit_bar)

        self.composer = ChatComposer()
        self.composer.setPlaceholderText("Ask liNi")
        self.composer.setAccessibleName("Message liNi")
        self.composer.setToolTip("Enter to send · Shift+Enter for a new line")
        self.composer.send_requested.connect(self._send)
        self.composer.focus_changed.connect(self._focus_composer)
        self.composer.textChanged.connect(self._composer_changed)
        frame.addWidget(self.composer)
        controls = QHBoxLayout()
        controls.setSpacing(3)
        self.provider = QComboBox()
        self.provider.setAccessibleName("Model provider")
        self.provider.currentIndexChanged.connect(self._select_provider)
        controls.addWidget(self.provider)
        self.writing = QPushButton("Writing tools")
        writing_menu = QMenu(self.writing)
        for task, (label, _) in WRITING_TASKS.items():
            writing_menu.addAction(label, lambda _checked=False, action=task: self._run_writing_tool(action))
        self.writing.setMenu(writing_menu)
        controls.addWidget(self.writing)
        controls.addStretch()
        if voice:
            mic = self._button("mic", "Hold to talk")
            mic.setObjectName("Microphone")
            mic.pressed.connect(lambda: voice.begin(submit=self._submit_chat))
            mic.released.connect(voice.release)
            controls.addWidget(mic)
            stop_voice = self._button("mute", "Stop speaking")
            stop_voice.clicked.connect(voice.stop_speaking)
            controls.addWidget(stop_voice)
        self.stop = self._button("stop", "Stop reply")
        self.stop.setObjectName("StopReply")
        self.stop.clicked.connect(self.desklet.clear_all)
        self.retry = self._button("retry", "Retry last message")
        self.retry.clicked.connect(self._retry)
        self.send = self._button("send", "Send message", "#1e162f")
        self.send.setObjectName("Send")
        self.send.clicked.connect(self._send)
        for button in (self.retry, self.stop, self.send):
            controls.addWidget(button)
        frame.addLayout(controls)
        column.addWidget(self.composer_frame)
        return self._center(activity)

    @staticmethod
    def _button(name, label, color="#b8bdd0"):
        button = QToolButton()
        button.setIcon(action_icon(name, color))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(36, 33)
        button.setToolTip(label)
        button.setAccessibleName(label)
        return button

    def _focus_composer(self, focused):
        frame = self.composer_frame
        frame.setProperty("focused", focused)
        frame.style().unpolish(frame)
        frame.style().polish(frame)

    def _composer_changed(self):
        lines = min(5, self.composer.blockCount())
        self.composer.setFixedHeight(max(34, self.composer.fontMetrics().lineSpacing() * lines + 11))
        ready = not self.desklet._busy and bool(self.composer.toPlainText().strip())
        self.send.setEnabled(ready)
        self.writing.setEnabled(ready and not self._editing)

    def refresh_providers(self):
        current = self.desklet._provider
        self.provider.blockSignals(True)
        try:
            self.provider.clear()
            for provider in enabled_providers(self.desklet._settings):
                self.provider.addItem(PROVIDER_LABELS[provider], provider)
            self.provider.setCurrentIndex(max(0, self.provider.findData(current)))
        finally:
            self.provider.blockSignals(False)

    def _select_provider(self):
        if provider := self.provider.currentData():
            self.desklet.select_provider(provider)

    def _refresh_list(self):
        active = self._chat_id
        scrollbar = self.items.verticalScrollBar()
        old_position = scrollbar.value()
        self.items.clear()
        for chat in self.store.list(self.search.text().strip()):
            item = QListWidgetItem(chat["title"])
            item.setToolTip(chat["title"])
            item.setData(Qt.ItemDataRole.UserRole, chat["id"])
            self.items.addItem(item)
            if chat["id"] == active:
                self.items.setCurrentItem(item)
        scrollbar.setValue(old_position)

    def _new(self):
        if not self.desklet._busy:
            self.composer.clear()
        self._select(None)

    def _select(self, chat):
        if self.desklet._busy:
            return
        self._finish_send_animation()
        self._chat_id = chat
        self._cancel_edit()
        self._partial = self._summary = ""
        self.progress.clear()
        self.refresh()
        self.composer.setFocus()

    def _rename(self):
        chat = self._chat_id
        if chat:
            title, accepted = QInputDialog.getText(self, "Rename chat", "Name")
            if accepted and title.strip():
                self.store.rename(chat, title)
                self.refresh()

    def _delete(self):
        chat = self._chat_id
        if not self.desklet._busy and chat and QMessageBox.question(
            self, "Delete chat", "Delete this conversation from this PC?",
        ) == QMessageBox.StandardButton.Yes:
            self.store.delete(chat)
            self._new()

    def _send(self):
        prompt = self.composer.toPlainText().strip()
        if prompt and not self.desklet._busy:
            task = self._editing_task
            if self._editing:
                self._chat_id = self.store.branch(*self._editing)
                self._editing = None
                self._editing_task = None
                self.edit_bar.hide()
            self._partial = self._summary = ""
            self._submit_chat(prompt, writing_task=task, clear_draft=True)

    def _run_writing_tool(self, task):
        text = self.composer.toPlainText().strip()
        if text and not self._editing and not self.desklet._busy:
            self._partial = self._summary = ""
            self._submit_chat(text, writing_task=task, clear_draft=True)

    def _submit_chat(self, prompt, source="chat", *, writing_task=None, clear_draft=False):
        options = {"writing_task": writing_task} if writing_task is not None else {}
        fresh = self._chat_id is None and writing_task is None
        if not self.desklet._busy:
            self._finish_send_animation()
            self._reset_stream()
            self._title_candidate = None
        if not self.desklet.submit_text(prompt, source, self.provider.currentData(), chat_id=self._chat_id, **options):
            return False
        active = self.store.active()
        changed_chat = active != self._chat_id
        glide = changed_chat and self._chat_id is None and self.isVisible() and self.workspace.motion_enabled
        if glide:
            origin = self.composer_frame.mapTo(self.workspace, QPoint())
            welcome_ghost = self._snapshot(self.welcome)
        self._chat_id = active
        if fresh and self.desklet._chat_turn:
            chat, message, provider, _ = self.desklet._chat_turn
            self._title_candidate = (chat, message, self.store.title(chat), prompt,
                                     self.desklet.provider_request_options(provider))
        if clear_draft:
            self.composer.clear()
        if changed_chat:
            self.refresh()
        if glide:
            self.welcome.parentWidget().layout().activate()
            self._start_send_animation(origin, welcome_ghost)
        return True

    def _snapshot(self, widget):
        ghost = QLabel(self.workspace)
        ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ratio = widget.devicePixelRatioF()
        image = QPixmap(ceil(widget.width() * ratio), ceil(widget.height() * ratio))
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image, flags=QWidget.RenderFlag.DrawChildren)
        ghost.setPixmap(image)
        ghost.resize(widget.size())
        ghost.move(widget.mapTo(self.workspace, QPoint()))
        return ghost

    def _start_send_animation(self, origin, welcome_ghost):
        self._finish_send_animation()
        composer_ghost = self._snapshot(self.composer_frame)
        welcome_opacity = QGraphicsOpacityEffect(welcome_ghost, opacity=1)
        welcome_ghost.setGraphicsEffect(welcome_opacity)
        self.composer_frame.setGraphicsEffect(QGraphicsOpacityEffect(self.composer_frame, opacity=0))
        self._send_ghosts = (origin, composer_ghost, welcome_ghost)
        self._move_sent_composer(0)
        welcome_ghost.show()
        composer_ghost.show()
        self._send_animation.start()

    def _move_sent_composer(self, progress):
        if self._send_ghosts is None:
            return
        origin, composer_ghost, welcome_ghost = self._send_ghosts
        target = self.composer_frame.mapTo(self.workspace, QPoint())
        composer_ghost.move(QPointF(origin + (target - origin) * progress).toPoint())
        welcome_ghost.graphicsEffect().setOpacity(1 - progress)

    def _finish_send_animation(self):
        if self._send_ghosts is None:
            return
        self._send_animation.stop()
        _, composer_ghost, welcome_ghost = self._send_ghosts
        self._send_ghosts = None
        for ghost in (composer_ghost, welcome_ghost):
            ghost.hide()
            ghost.deleteLater()
        self.composer_frame.setGraphicsEffect(None)

    def _cancel_edit(self):
        if self._editing:
            self._editing = None
            self._editing_task = None
            self.edit_bar.hide()
            self.composer.clear()

    def _retry(self):
        rows = self.store.messages(self._chat_id)
        last = next((m for m in reversed(rows) if m["role"] == "user"), None)
        if last and not self.desklet._busy:
            self._chat_id = self.store.branch(last["chat"], last["id"])
            self._submit_chat(last["text"], writing_task=writing_task_for_source(last["source"]))

    def _show_thinking(self, checked):
        self.progress.setVisible(checked and not self.thinking.isHidden())

    def _status(self, status):
        if not self._current_turn_pending():
            return
        self._reset_stream()
        self.progress.setPlainText(status)
        self.thinking.show()
        self._show_thinking(self.thinking.isChecked())
        self._render()

    def _current_turn_pending(self):
        turn = self.desklet._chat_turn
        return self.desklet._busy and turn is not None and turn[0] == self._chat_id

    def _preview(self, kind, text):
        if not self._current_turn_pending():
            return
        if kind == "stream_reset":
            self._reset_stream()
            self._render()
        elif kind == "token":
            self._tokens.append(text)
            if not self._token_timer.isActive():
                self._token_timer.start()
        elif kind == "partial":
            self._partial = text
            if not self._render_timer.isActive():
                self._render_timer.start()
        else:
            self._summary = (self._summary + text)[-4000:]
            self.progress.setPlainText(self._summary)
            self.progress.moveCursor(QTextCursor.MoveOperation.End)
            self.thinking.show()
            self._show_thinking(self.thinking.isChecked())

    def _reset_stream(self):
        self._token_timer.stop()
        self._tokens.clear()
        self._draining_tokens = False
        self._partial = ""

    def _reveal_tokens(self):
        count = min(len(self._tokens), max(1, ceil(len(self._tokens) / 32)))
        self._partial += "".join(self._tokens.popleft() for _ in range(count))
        if not self._tokens:
            self._token_timer.stop()
            if self._draining_tokens:
                self._reset_stream()
        self._render(stream_update=True)

    def _finished(self, *_):
        candidate, self._title_candidate = self._title_candidate, None
        if candidate is not None:
            chat, message, *_ = candidate
            rows = self.store.messages(chat)
            if rows and rows[0]["id"] == message and rows[0]["state"] == "done":
                self._request_title(*candidate)
        self.progress.setPlainText(self._summary)
        self.thinking.setChecked(False)

    def _request_title(self, chat, message, fallback, prompt, provider_options):
        request = DeskletRequest(self)
        timeout = QTimer(request, singleShot=True, interval=12000)
        timeout.timeout.connect(request.cancel)

        def apply_title(event, title):
            timeout.stop()
            if event == "response" and title and self.store.rename(
                chat, title, expected=fallback, first_message=message,
            ):
                self.refresh()

        request.outcome.connect(apply_title)
        timeout.start()
        request.start_request({"operation": "title", "prompt": prompt, "options": {}, **provider_options})

    def refresh(self):
        self._refresh_list()
        title = self.store.title(self._chat_id)
        if title is None:
            self._chat_id = None
        title = title or "New chat"
        self.header.setText(title)
        self.header.setToolTip(title)
        empty = self._chat_id is None
        self.welcome_top.setVisible(empty)
        self.welcome.setVisible(empty)
        self.welcome_bottom.setVisible(empty)
        self.transcript_wrap.setVisible(not empty)
        busy = self.desklet._busy
        for widget in (self.new, self.header_new, self.items, self.provider):
            widget.setEnabled(not busy)
        for widget in (self.rename, self.delete, self.retry, self.chat_menu):
            widget.setEnabled(not busy and not empty)
        self.retry.setVisible(not busy and not empty)
        self.stop.setVisible(busy)
        self.stop.setEnabled(busy)
        self.send.setVisible(not busy)
        self._composer_changed()
        if not busy:
            if self._tokens and self.isVisible():
                rows = self.store.messages(self._chat_id)
                buffered = self._partial + "".join(self._tokens)
                self._draining_tokens = bool(
                    rows and rows[-1]["role"] == "assistant" and rows[-1]["state"] == "done"
                    and rows[-1]["text"].startswith(buffered.strip())
                )
            if not self._draining_tokens:
                self._reset_stream()
            self._render_timer.stop()
            self.thinking.setVisible(bool(self._summary) and not empty)
            self._show_thinking(self.thinking.isChecked())
        self._render()

    def _link(self, url):
        if url.host() == "zylo.invalid" and url.path() in self._actions:
            action, value = self._actions[url.path()]
            if action == "edit":
                if self.desklet._busy:
                    return
                self._editing = (value["chat"], value["id"])
                self._editing_task = writing_task_for_source(value["source"])
                self.edit_bar.show()
                self.composer.setPlainText(value["text"])
                self.composer.setFocus()
                self.composer.moveCursor(QTextCursor.MoveOperation.End)
            else:
                QApplication.clipboard().setText(value)
        elif url.scheme() in ("https", "http"):
            QDesktopServices.openUrl(url)

    def _action_link(self, path, icon, label, operation, value, *, math):
        self._actions[path] = (operation, value)
        return (
            f'<a class="action" href="https://zylo.invalid{path}" title="{label}" aria-label="{label}">'
            f'{action_image(icon if math else "blank")}</a>'
        )

    def _message_markup(self, message, *, math=False, live_reply=False, include_author=True):
        user = message["role"] == "user"
        ident = message["id"]
        text = message["text"]
        state = message["state"]
        label = "" if user else PROVIDER_LABELS.get(message["provider"], "liNi")
        task = writing_task_for_source(message["source"])
        detail = state.title() if state not in ("done", "pending") else WRITING_TASKS[task][0] if task else ""
        actions = []
        if user:
            actions.append(self._action_link(f"/edit/{ident}", "edit", "Edit and resend", "edit", message, math=math))
        elif ident is not None:
            actions.append(self._action_link(f"/copy/{ident}", "copy", "Copy reply", "copy", text, math=math))
        fence = chr(96) * 3
        if ident is not None:
            for number, code in enumerate(re.findall(fence + r"[^\n]*\n([\s\S]*?)" + fence, text), 1):
                actions.append(self._action_link(f"/code/{ident}/{number}", "copy", f"Copy code {number}", "copy", code, math=math))
        bubble_style = ""
        if user:
            prompt = html.escape(text).replace("\n", "<br>")
            bubble_style = f' style="--chat-bubble:{self.viewer.bubble_color.name()};--chat-ink:{self._bubble_ink}"'
            body = (f'<p style="margin:14px 18px 11px 50px;color:{self._bubble_ink}">'
                    f'<a name="zylo-user-message"></a>{prompt}</p>' if not math else prompt)
        else:
            render = markdown_html if ident is None else cached_markdown_html
            body = render(text, math=math)
        anchor = '<a name="lini-reply"></a>' if live_reply and not math else ""
        author = f'<p class="provider">{anchor}{html.escape(label)}</p>' if label and include_author else ""
        badge = f'<span class="state">{detail}</span>' if detail else ""
        align = ' align="right"' if user else ""
        role = "user" if user else "answer"
        if math and user and ident == self._live_message and self._prompt_started is not None:
            elapsed_ms = (monotonic() - self._prompt_started) * 1000
            if elapsed_ms < SEND_TRANSITION_MS:
                role += " prompt-enter"
                bubble_style = bubble_style[:-1] + f';animation-delay:-{int(elapsed_ms)}ms"'
        if math and live_reply and self._reply_started is not None:
            elapsed_ms = (monotonic() - self._reply_started) * 1000
            if elapsed_ms < REPLY_REVEAL_MS:
                role += " reply-enter"
                bubble_style = f' style="animation-delay:-{int(elapsed_ms)}ms"'
        footer = f'<p class="actions"{align}>{" ".join(actions)} {badge}</p>' if actions or badge else ""
        body_class = "message-body" if user else "message-body reply-text"
        return f'<article class="{role}"{bubble_style}>{author}<div class="{body_class}">{body}</div>{footer}</article>'

    def _render_markup(self, messages, *, pending, math=False):
        parts = []
        live = False
        for message in messages:
            if math and message["role"] == "user" and message["id"] == self._live_message:
                parts.append(f'<section class="live-turn" data-turn="{message["id"]}">')
                live = True
            live_reply = message is messages[-1] and message["role"] == "assistant" and self._reply_started is not None
            parts.append(self._message_markup(message, math=math, live_reply=live_reply))
        if pending and self._partial:
            preview = {"role": "assistant", "id": None, "text": self._partial, "state": "pending",
                       "provider": messages[-1]["provider"], "source": None}
            parts.append(self._message_markup(preview, math=math, live_reply=True))
        elif pending:
            parts.append(
                '<p class="thinking"><span class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>Thinking</p>'
                if math else '<p class="thinking"><a name="lini-thinking"></a>Thinking</p>'
            )
        if live:
            parts.append("</section>")
        return "".join(parts)

    def _render(self, *, stream_update=False):
        if not self.isVisible():
            return
        active = self._chat_id
        changed_chat = active != self._rendered_chat
        self._rendered_chat = active
        self._web_revision += 1
        if changed_chat:
            self._live_message = self._reply_started = None
            self._prompt_started = self.viewer.prompt_started = None
            if self._draining_tokens:
                self._reset_stream()
        self.workspace.set_busy(self._current_turn_pending() or self._draining_tokens)
        if active is None:
            self._pending_markup = ""
            if self._page_ready:
                self._web_timer.stop()
            self._transcript_stack.setCurrentWidget(self._native_transcript)
            return

        messages = self.store.messages(active)
        if self._draining_tokens:
            messages = messages[:-1]
        last = messages[-1] if messages else None
        pending = self._draining_tokens or (last is not None and last["state"] == "pending" and self._current_turn_pending())
        new_turn = pending and self._live_message != last["id"]
        if new_turn:
            self._live_message = last["id"]
            self._reply_started = None
            self._prompt_started = self.viewer.prompt_started = monotonic() if self.workspace.motion_enabled else None
            self._summary = ""
            self.thinking.setChecked(False)
        finished = last is not None and last["role"] == "assistant" and last["state"] == "done"
        if self.workspace.motion_enabled and self._live_message is not None and self._reply_started is None:
            if (pending and self._partial) or finished:
                self._reply_started = monotonic()
        color = QColor(self.desklet._settings.get("chatBubbleColor", DEFAULT_CHAT_BUBBLE_COLOR))
        self.viewer.bubble_color = color if color.isValid() else QColor(DEFAULT_CHAT_BUBBLE_COLOR)
        color = self.viewer.bubble_color
        rgb = (color.redF(), color.greenF(), color.blueF())
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        luminance = sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
        self._bubble_ink = "#ffffff" if luminance < 0.179 else "#000000"
        scrollbar = self.viewer.verticalScrollBar()
        old_position = scrollbar.value()
        follow = changed_chat or old_position >= scrollbar.maximum() - 32
        self.viewer.live_turn = self._live_message is not None
        self.viewer.reply_started = self._reply_started
        updated_tail = False
        if stream_update and not changed_chat and not new_turn and self.viewer._reply_block is not None:
            if pending and self._partial:
                updated_tail = self.viewer.update_reply_tail(
                    f'<div class="reply-text">{markdown_html(self._partial)}</div>', finished=False,
                )
            elif finished:
                updated_tail = self.viewer.update_reply_tail(
                    self._message_markup(last, include_author=False), finished=True,
                )
        if not updated_tail:
            self._actions = {}
            markup = self._render_markup(messages, pending=pending)
            self.viewer.setHtml(
                f'<html><head><style>{FALLBACK_TRANSCRIPT_STYLE}</style></head>'
                f'<body style="color:#eef0f7">{markup}</body></html>'
            )
        scrollbar.setValue(scrollbar.maximum() if follow and not self.viewer.live_turn else old_position)
        if new_turn:
            self.viewer.scroll_to_latest()
        elif self._partial and follow and not self.viewer.is_scrolling:
            self.viewer._animate_scroll(scrollbar.maximum())

        has_equations = self.has_math and (any(
            not match[0].startswith(chr(96))
            for message in messages for match in MATH_OR_CODE.finditer(message["text"])
        ) or any(not match[0].startswith(chr(96)) for match in MATH_OR_CODE.finditer(self._partial)))
        if not has_equations or changed_chat:
            self._transcript_stack.setCurrentWidget(self._native_transcript)
        if not has_equations:
            self._pending_markup = ""
            if self._page_ready:
                self._web_timer.stop()
            return

        self._jump_to_latest |= changed_chat
        self._pending_markup = self._render_markup(messages, pending=pending, math=True)
        if self._page_ready:
            self._render_web()
        elif not self._page_started:
            self._start_web_view()

    def _use_native_transcript(self, *_):
        self._web_timer.stop()
        self.has_math = self._page_ready = False
        self._transcript_stack.setCurrentWidget(self._native_transcript)

    def _web_ready(self, ok):
        if not self.has_math or self._page_ready:
            return
        if ok:
            self._page_ready = True
            self._update_web_theme()
            if self._pending_markup:
                self._render_web()
            else:
                self._web_timer.stop()
        else:
            self._use_native_transcript()

    def _render_web(self):
        value = json.dumps(self._pending_markup)
        jump = json.dumps(self._jump_to_latest)
        self._jump_to_latest = False
        revision = self._web_revision
        if not self._web_timer.isActive():
            self._web_timer.start(5000)
        self._web_view.page().runJavaScript(
            "(()=>{const pane=document.getElementById('transcript'); if(!pane)return false;"
            f"const markup={value}; const jump={jump};"
            f"const revision='{revision}'; pane.dataset.revision=revision;"
            "const follow=jump||window.scrollY+window.innerHeight>=document.body.scrollHeight-70;"
            "if(window.MathJax?.typesetClear) MathJax.typesetClear([pane]);"
            "pane.innerHTML=markup;"
            "const turn=pane.querySelector('.live-turn');"
            "const scroll=()=>{"
            "if(pane.dataset.revision!==revision)return;"
            "if(turn&&turn.dataset.turn!==window.liniTurn)"
            "window.scrollTo({top:Math.max(0,turn.offsetTop-16),"
            "behavior:document.documentElement.classList.contains('reduce-motion')?'instant':'smooth'});"
            "else if(follow&&(!turn||turn.scrollHeight>window.innerHeight))"
            "window.scrollTo(0,document.body.scrollHeight);"
            "window.liniTurn=turn?.dataset.turn;};"
            "const ready=window.MathJax?.startup?.promise;"
            "if(ready)ready.then(()=>MathJax.typesetPromise([pane])).then(scroll,scroll);"
            "else scroll();"
            "return true;})()",
            lambda ok: self._web_painted(ok, revision),
        )

    def _web_painted(self, ok, revision):
        if revision != self._web_revision or not self.has_math:
            return
        if ok:
            self._web_timer.stop()
            self._transcript_stack.setCurrentWidget(self._web_view)
        else:
            self._use_native_transcript()

    def showEvent(self, event):
        super().showEvent(event)
        self.workspace.set_running(not self.isMinimized())
        self.refresh()
        if self._chat_id is None:
            self.composer.setFocus()

    def hideEvent(self, event):
        self._finish_send_animation()
        if self._sidebar_animation.state() == QVariantAnimation.State.Running:
            self._sidebar_animation.stop()
            self._finish_sidebar_slide()
        self.workspace.set_running(False)
        if self._draining_tokens:
            self._reset_stream()
        self.viewer._scroll.stop()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.workspace.set_running(self.isVisible() and not self.isMinimized())

    def _sync_theme(self):
        self.workspace.set_colors(self.desklet.card.colors)
        if self._page_ready:
            self._update_web_theme()

    def _update_web_theme(self):
        colors = json.dumps([color.name() for color in self.workspace.colors])
        reduced = json.dumps(not self.workspace.motion_enabled)
        self._web_view.page().runJavaScript(
            "(()=>{const root=document.documentElement;"
            f"{colors}.forEach((color,i)=>root.style.setProperty('--accent-'+i,color));"
            f"root.classList.toggle('reduce-motion',{reduced});"
            "})()",
        )
