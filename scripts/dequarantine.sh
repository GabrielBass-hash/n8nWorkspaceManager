#!/usr/bin/env bash
# Remove the com.apple.quarantine flag from an n8n Launcher app that was
# downloaded through a browser, so macOS stops blocking the (validly signed,
# non-notarized) bundle. Programs on macOS 15+ re-apply this flag when the
# .dmg was opened from a quarantined download — when that happens, prefer the
# curl installer (scripts/install_macos.sh), which never quarantines.
#
# Usage:
#   bash dequarantine.sh                          # defaults to /Applications/n8n-launcher.app
#   bash dequarantine.sh /path/to/n8n-launcher.app
#   bash dequarantine.sh --help

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: dequarantine.sh [app-or-dmg-path]
Default target: /Applications/n8n-launcher.app
EOF
    exit 0
fi

target="${1:-/Applications/n8n-launcher.app}"

[[ -e "$target" ]] || { echo "Not found: $target" >&2; echo "Pass the path to the .app bundle or .dmg as the first argument." >&2; exit 1; }

echo "Removing quarantine attribute from: $target"
if ! xattr -dr com.apple.quarantine "$target" 2>/dev/null; then
    echo "No write permission on $target — retrying with sudo (you may be prompted for a password)."
    sudo xattr -dr com.apple.quarantine "$target"
fi

if xattr -l "$target" | grep -q 'com.apple.quarantine'; then
    echo "The quarantine attribute is still present on $target." >&2
    echo "If this is macOS 15+, the flag may come back from the .dmg itself —" >&2
    echo "install via curl instead: bash scripts/install_macos.sh" >&2
    exit 1
fi

echo "Done — launch n8n Launcher normally (double-click)."
