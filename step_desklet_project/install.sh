#!/bin/bash

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Make main executable
chmod +x main.py

# Create a launcher script that works even if the folder is renamed/moved
cat > step-desklet <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source "$SCRIPT_DIR/venv/bin/activate"
exec python3 "$SCRIPT_DIR/main.py" "$@"
LAUNCHER
chmod +x step-desklet

echo "Installation complete. Run './step-desklet' to start."
