#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3 >/dev/null; then
  echo "Python 3 is required. On Fedora run: sudo dnf install python3 python3-pip"
  exit 1
fi

exec "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/install-extension.sh"
