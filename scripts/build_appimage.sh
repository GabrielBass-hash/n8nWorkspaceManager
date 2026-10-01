#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_NAME="n8n-launcher"
ONE_DIR="dist/${APP_NAME}"
APPIMAGE="appimagetool-x86_64.AppImage"
APPIMAGE_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/${APPIMAGE}"
OUT="dist/${APP_NAME}-linux-x86_64.AppImage"

if [[ ! -x "$ONE_DIR/${APP_NAME}" ]]; then
  echo "error: $ONE_DIR not built - run 'python scripts/build.py' first" >&2
  exit 1
fi

# The onedir layout carries the Qt platform plugin next to the executable; a
# build without it opens no window, so the AppImage must never be assembled
# from a partial tree.
if ! find "$ONE_DIR" -type d -name platforms -print -quit | grep -q .; then
  echo "error: no Qt platform plugin under $ONE_DIR - refusing to package" >&2
  exit 1
fi

if [[ ! -f "assets/icon.png" ]]; then
  echo "error: assets/icon.png missing" >&2
  exit 1
fi

APPDIR="n8n-launcher.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -R "$ONE_DIR/." "$APPDIR/usr/bin/"

cp assets/icon.png "$APPDIR/${APP_NAME}.png"

cat > "$APPDIR/${APP_NAME}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=n8n Launcher
Exec=${APP_NAME}
Icon=${APP_NAME}
Categories=Utility;
Terminal=false
EOF

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
SELF="$(readlink -f "$0")"
HERE="${SELF%/*}"
export PATH="${HERE}/usr/bin:${PATH}"
exec "${HERE}/usr/bin/n8n-launcher" "$@"
EOF
chmod +x "$APPDIR/AppRun"

if [[ ! -f "$APPIMAGE" ]]; then
  echo "downloading $APPIMAGE ..."
  curl -fsSL -o "$APPIMAGE" "$APPIMAGE_URL"
  chmod +x "$APPIMAGE"
fi

ARCH=x86_64 ./"$APPIMAGE" --appimage-extract-and-run "$APPDIR" "$OUT"

rm -rf "$APPDIR"
echo "built $OUT"
