#!/bin/bash

# Remove autostart entry
rm -f ~/.config/autostart/step-desklet.desktop

# Remove config directory
rm -rf ~/.local/share/step_desklet

# Remove venv
rm -rf venv

# Remove generated launcher
rm -f step-desklet

echo "Uninstalled successfully."
