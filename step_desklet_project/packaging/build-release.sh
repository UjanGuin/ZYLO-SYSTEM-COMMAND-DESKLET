#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-1.0.0}"
OUTPUT_DIR="${1:-$SCRIPT_DIR/dist}"
NAME="zylo-desktop-assistant"
ARCHIVE="$NAME-$VERSION.tar.gz"
ASSET_DIR="$SCRIPT_DIR/assets"
if [[ ! -d "$ASSET_DIR" && -d "$SCRIPT_DIR/../assets" ]]; then
  ASSET_DIR="$SCRIPT_DIR/../assets"
fi
BUILD_TOP="$(mktemp -d)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$BUILD_TOP" "$STAGE"' EXIT

if ! command -v rpmbuild >/dev/null 2>&1; then
  echo "rpmbuild is required. On Fedora: sudo dnf install rpm-build"
  exit 1
fi

mkdir -p "$OUTPUT_DIR" "$BUILD_TOP/SOURCES" "$BUILD_TOP/SPECS"
mkdir -p "$STAGE/$NAME-$VERSION/assets"
for item in LICENSE README.md .gitignore extension_bridge.py gnome_extension \
  install-extension.sh install.sh requirements.txt setup_app.py step-desklet \
  step_desklet uninstall.sh packaging; do
  cp -a "$SCRIPT_DIR/$item" "$STAGE/$NAME-$VERSION/"
done
cp -a "$ASSET_DIR"/desktop-*.webp "$STAGE/$NAME-$VERSION/assets/"
find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE" -type d -empty -delete
find "$STAGE" -type f -name '*.py[co]' -delete

tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
  -C "$STAGE" -czf "$OUTPUT_DIR/$ARCHIVE" "$NAME-$VERSION"
cp "$SCRIPT_DIR/packaging/zylo.spec" "$BUILD_TOP/SPECS/zylo.spec"
cp "$OUTPUT_DIR/$ARCHIVE" "$BUILD_TOP/SOURCES/$ARCHIVE"

rpmbuild -ba --define "_topdir $BUILD_TOP" "$BUILD_TOP/SPECS/zylo.spec"
cp "$BUILD_TOP/RPMS/noarch/$NAME-$VERSION-"*.noarch.rpm "$OUTPUT_DIR/"
cp "$BUILD_TOP/SRPMS/$NAME-$VERSION-"*.src.rpm "$OUTPUT_DIR/"
(
  cd "$OUTPUT_DIR"
  sha256sum "$NAME-$VERSION-"*.noarch.rpm "$NAME-$VERSION-"*.src.rpm "$ARCHIVE" > SHA256SUMS
)

echo "Release artifacts created in $OUTPUT_DIR:"
find "$OUTPUT_DIR" -maxdepth 1 -type f -printf '  %f (%s bytes)\n' | sort
