#!/usr/bin/env bash
# Build the Rust launcher and copy the release binary into dist/.
#
# Unlike the old PyInstaller packager, iced resolves everything it needs from
# the binary itself: there is no Qt platform plugin directory to ship and no
# --onefile/onedir distinction to get wrong.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORKSPACE="rust/n8n-launcher-workspace"
BIN="$WORKSPACE/target/release/n8n-launcher"

cargo build --manifest-path "$WORKSPACE/Cargo.toml" --workspace --release

mkdir -p dist
cp "$BIN" dist/
if [[ -f "${BIN}.exe" ]]; then
  cp "${BIN}.exe" dist/
fi

echo "Built: dist/$(basename "$BIN")"
echo "Verify it with: dist/$(basename "$BIN") --self-test"
