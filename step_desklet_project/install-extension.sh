#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UUID="zylo-command@local"
SOURCE_EXTENSION="$SCRIPT_DIR/gnome_extension/$UUID"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
RUNTIME_DIR="$DATA_HOME/zylo-system-command-desklet"
EXTENSION_DIR="$DATA_HOME/gnome-shell/extensions/$UUID"

if ! command -v gnome-extensions >/dev/null || ! command -v python3 >/dev/null; then
  echo "GNOME Shell and Python 3 are required. On Fedora: sudo dnf install gnome-shell python3 python3-pip"
  exit 1
fi

if [[ ! -d "$SOURCE_EXTENSION" ]]; then
  echo "Extension source was not found: $SOURCE_EXTENSION"
  exit 1
fi

install -d "$RUNTIME_DIR/step_desklet" "$RUNTIME_DIR/gnome_extension/$UUID" "$EXTENSION_DIR"
if [[ "$(realpath -m "$SCRIPT_DIR")" != "$(realpath -m "$RUNTIME_DIR")" ]]; then
  cp -a "$SCRIPT_DIR/step_desklet/." "$RUNTIME_DIR/step_desklet/"
  install -m 755 "$SCRIPT_DIR/extension_bridge.py" "$RUNTIME_DIR/extension_bridge.py"
  install -m 755 "$SCRIPT_DIR/setup_app.py" "$RUNTIME_DIR/setup_app.py"
  install -m 644 "$SCRIPT_DIR/requirements.txt" "$RUNTIME_DIR/requirements.txt"
  install -m 755 "$SCRIPT_DIR/install-extension.sh" "$RUNTIME_DIR/install-extension.sh"
  cp -a "$SOURCE_EXTENSION/." "$RUNTIME_DIR/gnome_extension/$UUID/"
fi
if [[ "$(realpath -m "$SOURCE_EXTENSION")" != "$(realpath -m "$EXTENSION_DIR")" ]]; then
  cp -a "$SOURCE_EXTENSION/." "$RUNTIME_DIR/gnome_extension/$UUID/"
fi

python3 -m venv "$RUNTIME_DIR/venv"
"$RUNTIME_DIR/venv/bin/python" -m pip install --upgrade pip
"$RUNTIME_DIR/venv/bin/python" -m pip install -r "$RUNTIME_DIR/requirements.txt"

# Prevent the legacy PyQt launcher from creating a second, normal application window.
rm -f "$HOME/.config/autostart/step-desklet.desktop"
bundle_dir="$(mktemp -d)"
trap 'rm -rf "$bundle_dir"' EXIT
gnome-extensions pack --force --out-dir "$bundle_dir" "$SOURCE_EXTENSION"
bundle_path="$(find "$bundle_dir" -maxdepth 1 -name '*.shell-extension.zip' -print -quit)"
if [[ -z "$bundle_path" ]]; then
  echo "GNOME Shell extension bundle could not be created."
  exit 1
fi
gnome-extensions install --force "$bundle_path"
enabled_extensions="$(gsettings get org.gnome.shell enabled-extensions)"
if [[ "$enabled_extensions" != *"'$UUID'"* ]]; then
  if [[ "$enabled_extensions" == "@as []" || "$enabled_extensions" == "[]" ]]; then
    gsettings set org.gnome.shell enabled-extensions "['$UUID']"
  else
    gsettings set org.gnome.shell enabled-extensions "${enabled_extensions%]}, '$UUID']"
  fi
fi
gnome-extensions enable "$UUID" || true

APP_DIR="$HOME/.local/share/applications"
install -d "$APP_DIR"
cat > "$APP_DIR/zylo.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ZYLO Desktop Assistant
Comment=Configure and launch your floating desktop assistant
Exec=$RUNTIME_DIR/venv/bin/python $RUNTIME_DIR/setup_app.py
Icon=applications-system
Terminal=false
Categories=Utility;
StartupNotify=true
DESKTOP
chmod 644 "$APP_DIR/zylo.desktop"

# GNOME crash recovery can disable every user extension after a Shell timeout.
# Restore the saved user-extension setting at the next login if that happened.
cat > "$RUNTIME_DIR/restore-extensions.sh" <<'RECOVERY'
#!/usr/bin/env bash
set -u
sleep 6
if command -v gsettings >/dev/null 2>&1; then
  extensions_disabled="$(gsettings get org.gnome.shell disable-user-extensions 2>/dev/null || true)"
  if [[ "$extensions_disabled" == "true" ]]; then
    gsettings set org.gnome.shell disable-user-extensions false
  fi
fi
RECOVERY
chmod 755 "$RUNTIME_DIR/restore-extensions.sh"
AUTOSTART_DIR="$HOME/.config/autostart"
install -d "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/zylo-restore-extensions.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Restore GNOME extensions
Comment=Restore the saved extension list if GNOME Shell recovery disabled extensions
Exec="$RUNTIME_DIR/restore-extensions.sh"
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
DESKTOP
chmod 644 "$AUTOSTART_DIR/zylo-restore-extensions.desktop"

echo "ZYLO is installed and marked enabled as a GNOME Shell extension."
echo "Log out and back in once to load the updated extension in this running session."
echo "Drag the ZYLO header to move it; use the padlock icon to lock or unlock it."
