"""Visual styles and action icons shared by the Chats controls and transcript."""

from functools import lru_cache

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


_ICONS = {
    "blank": "",
    "sidebar": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M9 5v14"/>',
    "compose": '<path d="M12 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-6M10 14l1-4 7-7 3 3-7 7z"/>',
    "new": '<path d="M12 5v14M5 12h14"/>',
    "more": '<path d="M5 12h.01m6.99 0h.01m6.99 0h.01" stroke-width="3"/>',
    "edit": '<path d="M13.8 5.2l5 5M4 20l4.8-1 11-11a1.8 1.8 0 0 0 0-2.6l-1.2-1.2a1.8 1.8 0 0 0-2.6 0L5 15.2z"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 4H6a2 2 0 0 0-2 2v10"/>',
    "send": '<path d="M12 19V5m-6 6l6-6 6 6"/>',
    "mic": '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 11v1a6 6 0 0 0 12 0v-1m-6 7v3m-3 0h6"/>',
    "mute": '<path d="M4 10h4l5-4v12l-5-4H4zm12 0 4 4m0-4-4 4"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>',
    "retry": '<path d="M4 10a8 8 0 1 1 2 7m-2-7V5m0 5h5"/>',
    "cancel": '<path d="M6 6l12 12M6 18 18 6"/>',
    "chevron": '<path d="m6 9 6 6 6-6"/>',
}


@lru_cache
def icon_pixmap(name, size=18, color="#b8bdd0"):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" color="{color}" '
        f'fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" '
        f'stroke-linejoin="round">{_ICONS[name]}</svg>'
    )
    image = QPixmap(size * 2, size * 2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg.encode("ascii"))).render(painter)
    painter.end()
    image.setDevicePixelRatio(2)
    return image


def action_icon(name, color="#b8bdd0"):
    icon = QIcon()
    icon.addPixmap(icon_pixmap(name, color=color))
    icon.addPixmap(icon_pixmap(name, color="#626476"), QIcon.Mode.Disabled)
    return icon


@lru_cache
def action_image(name):
    data = QBuffer()
    data.open(QIODevice.OpenModeFlag.WriteOnly)
    icon_pixmap(name).save(data, "PNG")
    encoded = bytes(data.data().toBase64()).decode("ascii")
    return f'<img src="data:image/png;base64,{encoded}" width="18" height="18" />'


CHAT_STYLE = """
    QMainWindow { background: #14161f; color: #eef0f7; }
    QLabel, QLineEdit, QPlainTextEdit, QListWidget, QPushButton, QComboBox, QMenu { font-family: "Segoe UI"; }
    QLabel, QLineEdit, QPlainTextEdit, QListWidget, QComboBox { color: #eef0f7; }
    QWidget#History { background: rgba(25,27,40,230);
        border: 1px solid rgba(155,145,185,51); border-radius: 21px; }
    QLabel#Brand { font-size: 18px; font-weight: 700; }
    QLabel#HistoryLabel { color: #9c9fb8; font-size: 12px; font-weight: 600; }
    QLabel#ChatTitle { font-size: 15px; font-weight: 600; }
    QLabel#Welcome { font-size: 32px; font-weight: 600; color: #f2efff; }
    QFrame#Composer { background: #222533; border: 1px solid #3b3e55; border-radius: 18px; }
    QFrame#Composer[focused="true"] { border-color: #8b78bf; }
    QPlainTextEdit, QTextBrowser { background: transparent; border: none; selection-background-color: #584781; }
    QPlainTextEdit { font-size: 15px; padding: 0; }
    QLineEdit { background: #171923; border: 1px solid #333549; border-radius: 8px; padding: 8px 10px; }
    QLineEdit:focus { border-color: #8b78bf; }
    QListWidget { background: transparent; border: none; outline: none; font-size: 13px; }
    QListWidget::item { padding: 8px 10px; margin: 1px 0; border-radius: 7px; }
    QListWidget::item:hover { background: #242638; }
    QListWidget::item:selected { background: #312b46; color: #f2eaff; }
    QPushButton, QToolButton, QComboBox { background: transparent; color: #d9dcef; border: 1px solid transparent;
        border-radius: 8px; font-size: 13px; padding: 5px 8px; }
    QPushButton:hover, QToolButton:hover, QComboBox:hover { background: #303348; }
    QPushButton:focus, QToolButton:focus, QComboBox:focus { border-color: #8b78bf; }
    QPushButton:disabled, QToolButton:disabled, QComboBox:disabled { color: #777a90; }
    QPushButton#NewChat { text-align: left; background: #29243d; border-color: #3f3756; padding: 8px 10px; }
    QPushButton#NewChat:hover { background: #37304f; }
    QToolButton#Send { background: #c3acf9; color: #191224; border-radius: 10px; }
    QToolButton#Send:hover { background: #d6c5ff; }
    QToolButton#Send:disabled { background: #303247; }
    QToolButton#StopReply { background: #392f50; border-radius: 10px; }
    QToolButton#StopReply:hover { background: #4c3e68; }
    QToolButton#Microphone:pressed { background: #504167; border-color: #b399ed; }
    QToolButton::menu-indicator { image: none; }
    QComboBox { padding-right: 20px; }
    QComboBox::drop-down { width: 19px; border: none; }
    QComboBox::down-arrow { image: url("@CHEVRON@"); width: 14px; height: 14px; }
    QComboBox QAbstractItemView, QMenu { background: #242638; color: #ecebfa; border: 1px solid #414359;
        selection-background-color: #403657; padding: 4px; }
    QMenu::item { padding: 7px 18px; }
    QMenu::item:selected { background: #403657; border-radius: 5px; }
    QMenu::item:disabled { color: #85879d; }
    QLabel#Activity { color: #adb0c7; font-size: 13px; }
    QSplitter::handle { background: transparent; }
    QScrollBar:vertical { border: none; background: transparent; width: 8px; margin: 3px 0; }
    QScrollBar::handle:vertical { background: #464960; min-height: 24px; border-radius: 4px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QWidget#History QScrollBar:vertical { width: 4px; }
    QWidget#History QScrollBar::handle:vertical { border-radius: 2px; }
"""


TRANSCRIPT_STYLE = """
    :root { color-scheme: dark; scrollbar-width: none;
        --accent-0: #a78bfa; --accent-1: #38bdf8; --accent-2: #f472b6; }
    * { box-sizing: border-box; }
    body { background: transparent; color: #eef0f7; font: 15px/1.65 "Segoe UI", sans-serif; margin: 0;
        overflow-anchor: none; }
    #transcript { width: min(100%, 850px); margin: 0 auto; padding: 34px 28px 24px; }
    .live-turn { display: flow-root; min-height: calc(100vh - 40px); }
    .thinking { display: flex; align-items: center; gap: 10px; color: #cfcede; margin: 14px 0 24px; }
    .thinking-dots { display: inline-flex; gap: 4px; }
    .thinking-dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--accent-0);
        animation: thinking-pulse 1.15s ease-in-out infinite; }
    .thinking-dots i:nth-child(2) { background: var(--accent-1); animation-delay: -.18s; }
    .thinking-dots i:nth-child(3) { background: var(--accent-2); animation-delay: -.36s; }
    @keyframes thinking-pulse { 0%, 100% { opacity: .45; transform: translateY(0); }
        50% { opacity: 1; transform: translateY(-2px); } }
    .reply-enter { animation: reply-in .32s cubic-bezier(.22, .75, .36, 1) both; }
    .prompt-enter { animation: reply-in .30s cubic-bezier(.22, .75, .36, 1) both; }
    @keyframes reply-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
    .reduce-motion .thinking-dots i, .reduce-motion .reply-enter, .reduce-motion .prompt-enter { animation: none; }
    @media (prefers-reduced-motion: reduce) { .thinking-dots i, .reply-enter, .prompt-enter { animation: none; } }
    ::-webkit-scrollbar { width: 9px; height: 9px; }
    ::-webkit-scrollbar-thumb { background: #44465b; border-radius: 6px; border: 2px solid #14161f; }
    article { margin-bottom: 28px; overflow-wrap: anywhere; }
    .user { margin-left: auto; width: fit-content; max-width: 82%; }
    .user .message-body { background: var(--chat-bubble); color: var(--chat-ink); border-radius: 19px;
        padding: 9px 15px; }
    .answer .message-body { font-family: "Segoe UI", sans-serif; font-size: 16px; }
    .message-body > :first-child { margin-top: 0 !important; }
    .message-body > :last-child { margin-bottom: 0 !important; }
    .provider { color: #b3a1d9; font-size: 12px; font-weight: 650; margin: 0 0 6px; }
    .state { color: #adaec8; font-size: 12px; margin-left: 8px; }
    .actions { margin-top: 7px; display: flex; gap: 5px; }
    .user .actions { justify-content: flex-end; }
    .action { display: inline-grid; place-items: center; width: 30px; height: 28px;
        border-radius: 7px; opacity: .8; text-decoration: none; }
    .action:hover, .action:focus-visible { background: #303348; opacity: 1; }
    .actions .action { opacity: 0; transition: opacity .14s ease; }
    article:hover .action, .action:focus-visible { opacity: .85; }
    a { color: #d0baff; }
    h1, h2, h3, h4 { color: #faf7ff; line-height: 1.3; }
    table { border-collapse: collapse; display: block; overflow: auto; }
    th, td { border: 1px solid #414459; padding: 6px 10px; }
    th { background: #222535; }
    pre { background: #1c1f2d; color: #d1e0fa; padding: 12px 15px; border-radius: 9px; overflow: auto; }
    code { font-family: Consolas, monospace; }
    blockquote { margin-left: 0; padding-left: 14px; border-left: 2px solid #77639f; }
    mjx-container[display="true"] { overflow: auto; padding: 4px 0; }
"""


FALLBACK_TRANSCRIPT_STYLE = """
    body { font-family: "Segoe UI"; font-size: 15px; }
    div.reply-text { font-family: "Segoe UI"; font-size: 16px; }
    p.provider { color: #b3a1d9; font-size: 12px; font-weight: 600; margin-top: 18px; margin-bottom: 8px; }
    p.thinking { color: #cfcede; margin: 14px 0 24px 32px; }
    p.actions { margin-top: 7px; margin-bottom: 16px; }
    h1, h2, h3, h4 { color: #faf7ff; }
    a { color: #d0baff; }
    td, th { border: 1px solid #414459; padding: 6px 10px; }
    th, pre { background: #242638; }
    pre, code { color: #d1e0fa; font-family: Consolas; }
"""
