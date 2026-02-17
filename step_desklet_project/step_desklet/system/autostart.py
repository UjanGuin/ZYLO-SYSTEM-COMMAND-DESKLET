import os

AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
DESKTOP_ENTRY_FILE = os.path.join(AUTOSTART_DIR, "step-desklet.desktop")


def _quote_exec_path(path: str) -> str:
    # Desktop Exec field supports quoted arguments; this keeps paths with spaces valid.
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def setup_autostart(app_path: str):
    os.makedirs(AUTOSTART_DIR, exist_ok=True)

    content = f"""[Desktop Entry]
Type=Application
Exec={_quote_exec_path(app_path)}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
    Name=ZYLO-SYSTEM-COMMAND-DESKLET
Comment=Autonomous AI Guardian Desklet
Terminal=false
StartupNotify=false
"""
    with open(DESKTOP_ENTRY_FILE, "w", encoding="utf-8") as f:
        f.write(content)


def remove_autostart():
    if os.path.exists(DESKTOP_ENTRY_FILE):
        os.remove(DESKTOP_ENTRY_FILE)
