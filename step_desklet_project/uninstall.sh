#!/usr/bin/env bash
set -euo pipefail

UUID="zylo-command@local"
RUNTIME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/zylo-system-command-desklet"

if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions disable "$UUID" >/dev/null 2>&1 || true
  gnome-extensions uninstall "$UUID" >/dev/null 2>&1 || true
fi

rm -f "$HOME/.local/share/applications/zylo.desktop"
rm -f "$HOME/.config/autostart/zylo-restore-extensions.desktop"
rm -rf "$RUNTIME_DIR"

echo "ZYLO has been removed. Your settings, conversation history, layout, and API keys were kept in ~/.config/step-desklet and ~/.config/step-desklet.env."
