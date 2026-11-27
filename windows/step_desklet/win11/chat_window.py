"""The full conversation window for the Windows desklet."""

import html
import json
import re
from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor, QDesktopServices, QFont, QFontMetricsF, QKeySequence, QPainter, QShortcut, QTextCursor,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from step_desklet.core.config import DEFAULT_CHAT_BUBBLE_COLOR, PROVIDER_LABELS, enabled_providers
from step_desklet.core.assistant_context import WRITING_TASKS, writing_task_for_source
from step_desklet.win11.branding import zylo_app_icon
from step_desklet.win11.chat_style import (
    CHAT_STYLE, FALLBACK_TRANSCRIPT_STYLE, TRANSCRIPT_STYLE, action_icon, action_image,
)

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


def normalize_markdown_tables(text):
    if "|" not in text:
        return text
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
        elif not fence and not in_table and has_pipe and index + 1 < len(lines):
            divider = lines[index + 1].strip()
            if "|" in divider and TABLE_SEPARATOR.fullmatch(divider):
                if output and output[-1].strip():
                    output.append("")
                in_table = True
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

    text = normalize_markdown_tables(text)
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


if QWebEngineView is not None:
    class TranscriptPage(QWebEnginePage):
        action = pyqtSignal(QUrl)

        def acceptNavigationRequest(self, url, kind, main_frame):
            if kind == self.NavigationType.NavigationTypeLinkClicked:
                self.action.emit(url)
                return False
            return url.scheme() in ("data", "file", "about")


class ChatTranscript(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.bubble_color = QColor(DEFAULT_CHAT_BUBBLE_COLOR)
        self._bubble_blocks = []

    def setHtml(self, markup):
        self._bubble_blocks = []
        super().setHtml(markup)
        block = self.document().begin()
        while block.isValid():
            first = block.begin().fragment()
            if first.isValid() and "zylo-user-message" in first.charFormat().anchorNames():
                self._bubble_blocks.append(block)
            block = block.next()
        self._align_bubble_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._align_bubble_text()

    def _align_bubble_text(self):
        available = self.viewport().width() - 2 * self.document().documentMargin()
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

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bubble_color)
        offset_y = self.verticalScrollBar().value()
        offset_x = self.horizontalScrollBar().value()
        layout = self.document().documentLayout()
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
        painter.end()
        super().paintEvent(event)


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
        self._jump_to_latest = False
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(65)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)

        container = QSplitter()
        container.setChildrenCollapsible(False)
        container.setHandleWidth(1)
        self.setCentralWidget(container)
        container.addWidget(self._build_sidebar())
        main = QWidget()
        main.setObjectName("Workspace")
        right = QVBoxLayout(main)
        right.setContentsMargins(0, 0, 0, 16)
        right.setSpacing(0)
        right.addWidget(self._build_header())
        self.transcript_wrap = self._build_transcript()
        right.addWidget(self.transcript_wrap, 1)
        self.welcome = self._build_welcome()
        right.addWidget(self.welcome, 3)
        right.addWidget(self._build_composer(voice))
        self.welcome_bottom = QWidget()
        right.addWidget(self.welcome_bottom, 2)
        container.addWidget(main)
        container.setSizes([238, 922])
        container.setStretchFactor(0, 0)
        container.setStretchFactor(1, 1)

        refresh = QShortcut(QKeySequence.StandardKey.Refresh, self)
        refresh.activated.connect(self.refresh)
        desklet.chat_changed.connect(self.refresh)
        desklet.chat_status.connect(self._status)
        desklet.chat_preview.connect(self._preview)
        desklet.chat_finished.connect(self._finished)
        desklet.provider_changed.connect(lambda _: self.refresh_providers())
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
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.itemActivated.connect(self._activate_item)
        self.items.itemClicked.connect(self._activate_item)
        left.addWidget(self.items, 1)
        return sidebar

    def _activate_item(self, item):
        self._select(item.data(Qt.ItemDataRole.UserRole))

    def _build_header(self):
        header = QWidget()
        header.setObjectName("ChatHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 8, 16, 8)
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

    def _build_transcript(self):
        self.has_math = QWebEngineView is not None and (self.assets_dir / "tex-svg.js").is_file()
        self.viewer = ChatTranscript()
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
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.setBackgroundColor(QColor("#14161f"))
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
            '<script defer src="tex-svg.js"></script><main id="transcript"></main>'
        )
        view.setHtml(f"<style>{TRANSCRIPT_STYLE}</style>{shell}", QUrl.fromLocalFile(str(self.assets_dir) + "/"))

    @staticmethod
    def _build_welcome():
        welcome = QWidget()
        column = QVBoxLayout(welcome)
        column.setContentsMargins(16, 0, 16, 20)
        column.setSpacing(12)
        column.addStretch(1)
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
            if self._submit_chat(prompt, writing_task=task):
                self.composer.clear()

    def _run_writing_tool(self, task):
        text = self.composer.toPlainText().strip()
        if text and not self._editing and not self.desklet._busy:
            self._partial = self._summary = ""
            if self._submit_chat(text, writing_task=task):
                self.composer.clear()

    def _submit_chat(self, prompt, source="chat", *, writing_task=None):
        options = {"writing_task": writing_task} if writing_task is not None else {}
        if not self.desklet.submit_text(prompt, source, self.provider.currentData(), chat_id=self._chat_id, **options):
            return False
        self._chat_id = self.store.active()
        self.refresh()
        return True

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
        self._partial = ""
        self.progress.setPlainText(status)
        self.thinking.show()
        self.thinking.setChecked(True)
        self._show_thinking(True)
        self._render()

    def _preview(self, kind, text):
        if kind == "partial":
            self._partial = text
            if not self._render_timer.isActive():
                self._render_timer.start()
        else:
            self._summary = (self._summary + text)[-4000:]
            self.progress.setPlainText(self._summary)
            self.progress.moveCursor(QTextCursor.MoveOperation.End)

    def _finished(self, *_):
        self._partial = ""
        self.progress.setPlainText(self._summary)
        self.thinking.setChecked(False)
        self.refresh()

    def refresh(self):
        self._refresh_list()
        title = self.store.title(self._chat_id)
        if title is None:
            self._chat_id = None
        title = title or "New chat"
        self.header.setText(title)
        self.header.setToolTip(title)
        empty = self._chat_id is None
        self.welcome.setVisible(empty)
        self.welcome_bottom.setVisible(empty)
        self.transcript_wrap.setVisible(not empty)
        busy = self.desklet._busy
        for widget in (self.new, self.items, self.provider):
            widget.setEnabled(not busy)
        for widget in (self.rename, self.delete, self.retry, self.chat_menu):
            widget.setEnabled(not busy and not empty)
        self.retry.setVisible(not busy and not empty)
        self.stop.setVisible(busy)
        self.stop.setEnabled(busy)
        self.send.setVisible(not busy)
        self._composer_changed()
        if not busy:
            self._partial = ""
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

    def _action_link(self, path, icon, label, operation, value):
        self._actions[path] = (operation, value)
        return (
            f'<a class="action" href="https://zylo.invalid{path}" title="{label}" aria-label="{label}">'
            f'{action_image(icon)}</a>'
        )

    def _message_markup(self, message, *, math=False):
        user = message["role"] == "user"
        ident = message["id"]
        text = message["text"]
        state = message["state"]
        label = "" if user else PROVIDER_LABELS.get(message["provider"], "liNi")
        task = writing_task_for_source(message["source"])
        detail = state.title() if state not in ("done", "pending") else WRITING_TASKS[task][0] if task else ""
        actions = []
        if user:
            actions.append(self._action_link(f"/edit/{ident}", "edit", "Edit and resend", "edit", message))
        else:
            actions.append(self._action_link(f"/copy/{ident}", "copy", "Copy reply", "copy", text))
        fence = chr(96) * 3
        for number, code in enumerate(re.findall(fence + r"[^\n]*\n([\s\S]*?)" + fence, text), 1):
            actions.append(self._action_link(f"/code/{ident}/{number}", "copy", f"Copy code {number}", "copy", code))
        bubble_style = ""
        if user:
            prompt = html.escape(text).replace("\n", "<br>")
            bubble_style = f' style="--chat-bubble:{self.viewer.bubble_color.name()};--chat-ink:{self._bubble_ink}"'
            body = (f'<p style="margin:14px 18px 11px 50px;color:{self._bubble_ink}">'
                    f'<a name="zylo-user-message"></a>{prompt}</p>' if not math else prompt)
        else:
            body = markdown_html(text, math=math)
        author = f'<p class="provider">{html.escape(label)}</p>' if label else ""
        badge = f'<span class="state">{detail}</span>' if detail else ""
        align = ' align="right"' if user else ""
        role = "user" if user else "answer"
        return (f'<article class="{role}"{bubble_style}>{author}<div class="message-body">{body}</div>'
                f'<p class="actions"{align}>{" ".join(actions)} {badge}</p></article>')

    def _render_markup(self, messages, *, math=False):
        parts = [self._message_markup(message, math=math) for message in messages]
        if self._partial:
            parts.append('<article class="answer"><div class="message-body">'
                         + markdown_html(self._partial, math=math) + '</div></article>')
        return "".join(parts)

    def _render(self):
        if not self.isVisible():
            return
        active = self._chat_id
        changed_chat = active != self._rendered_chat
        self._rendered_chat = active
        self._web_revision += 1
        if active is None:
            self._pending_markup = ""
            if self._page_ready:
                self._web_timer.stop()
            self._transcript_stack.setCurrentWidget(self._native_transcript)
            return

        messages = self.store.messages(active)
        color = QColor(self.desklet._settings.get("chatBubbleColor", DEFAULT_CHAT_BUBBLE_COLOR))
        self.viewer.bubble_color = color if color.isValid() else QColor(DEFAULT_CHAT_BUBBLE_COLOR)
        color = self.viewer.bubble_color
        rgb = (color.redF(), color.greenF(), color.blueF())
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        luminance = sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
        self._bubble_ink = "#ffffff" if luminance < 0.179 else "#000000"
        self._actions = {}
        markup = self._render_markup(messages)
        scrollbar = self.viewer.verticalScrollBar()
        old_position = scrollbar.value()
        follow = changed_chat or old_position >= scrollbar.maximum() - 48
        self.viewer.setHtml(
            f'<html><head><style>{FALLBACK_TRANSCRIPT_STYLE}</style></head>'
            f'<body style="color:#eef0f7">{markup}</body></html>'
        )
        scrollbar.setValue(scrollbar.maximum() if follow else old_position)

        has_equations = self.has_math and any(
            not match[0].startswith(chr(96))
            for message in messages for match in MATH_OR_CODE.finditer(message["text"])
        )
        if not has_equations or changed_chat:
            self._transcript_stack.setCurrentWidget(self._native_transcript)
        if not has_equations:
            self._pending_markup = ""
            if self._page_ready:
                self._web_timer.stop()
            return

        self._jump_to_latest |= changed_chat
        self._pending_markup = self._render_markup(messages, math=True)
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
            "const follow=jump||window.scrollY+window.innerHeight>=document.body.scrollHeight-70;"
            "if(window.MathJax?.typesetClear) MathJax.typesetClear([pane]);"
            "pane.innerHTML=markup;"
            "const scroll=()=>{if(follow)window.scrollTo(0,document.body.scrollHeight);};"
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
        self.refresh()
        if self._chat_id is None:
            self.composer.setFocus()
