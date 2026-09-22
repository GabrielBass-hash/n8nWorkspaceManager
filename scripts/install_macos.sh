#!/usr/bin/env bash
# Install n8n Launcher on macOS without the Gatekeeper "unidentified
# developer" dialog, and without paying for the Apple Developer Program.
#
# Why this works: Gatekeeper only blocks apps tagged with the
# com.apple.quarantine attribute, which browsers/Finder add on download.
# A file pulled down with `curl` in a terminal carries no such attribute, so
# a curl-based installer sails through even though the app is not notarized.
#
# Usage:
#   curl -fsSL https://github.com/GabrielBass-hash/n8nWorkspaceManager/releases/latest/download/install_macos.sh | bash
#   bash install_macos.sh -f dist/n8n-launcher-macos.dmg   # install a local build
#   bash install_macos.sh -d ~/Applications -f dist/n8n-launcher-macos.dmg
#   bash install_macos.sh -n -f dist/n8n-launcher-macos.dmg  # dry run, no changes

set -euo pipefail

REPO_URL="https://github.com/GabrielBass-hash/n8nWorkspaceManager"
DMG_URL="$REPO_URL/releases/latest/download/n8n-launcher-macos.dmg"
APP_NAME="n8n-launcher.app"
APP_BIN="n8n-launcher"

file_arg=""
dest_dir="/Applications"
dry_run=0
assume_yes=0

usage() {
    cat <<'EOF'
Usage: install_macos.sh [options]

Options:
  -f <dmg>   Install from a local .dmg instead of downloading the release
  -d <dir>   Destination directory (default: /Applications)
  -n         Dry run: print what would happen, change nothing
  -y         Skip the confirmation prompt (piped installs skip it anyway)
  -h         Show this help
EOF
}

while getopts "f:d:nyh" opt; do
    case "$opt" in
        f) file_arg="$OPTARG" ;;
        d) dest_dir="$OPTARG" ;;
        n) dry_run=1 ;;
        y) assume_yes=1 ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

# When stdin is a pipe (`curl ... | bash`) we cannot prompt, so never wait.
if [[ ! -t 0 ]]; then
    assume_yes=1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This installer is macOS-only." >&2
    exit 1
fi

app_path="$dest_dir/$APP_NAME"
if [[ -n "$file_arg" ]]; then
    source_label="local $file_arg"
else
    source_label="download:$DMG_URL"
fi

if [[ "$dry_run" -eq 1 ]]; then
    echo "Dry run — would install:"
    echo "  source: $source_label"
    echo "  target: $app_path"
    echo "  steps:  $( [[ -z "$file_arg" ]] && echo 'curl download → ' )hdiutil attach → ditto → xattr -dr com.apple.quarantine → codesign --verify"
    exit 0
fi

if [[ "$assume_yes" -ne 1 ]]; then
    echo "This will install (replacing any existing copy): $app_path"
    read -rp "Continue? [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]] || { echo "Aborted."; exit 1; }
fi

tmp_dir="$(mktemp -d)"
mount_point="$tmp_dir/mnt"
mkdir -p "$mount_point"
trap 'if mount | grep -qF "$mount_point"; then hdiutil detach "$mount_point" >/dev/null 2>&1 || true; fi; rm -rf "$tmp_dir"' EXIT

if [[ -z "$file_arg" ]]; then
    dmg="$tmp_dir/n8n-launcher-macos.dmg"
    echo "Downloading $DMG_URL ..."
    curl -fL --retry 3 --progress-bar -o "$dmg" "$DMG_URL" || { echo "Download failed." >&2; exit 1; }
else
    dmg="$file_arg"
    [[ -f "$dmg" ]] || { echo "DMG not found: $dmg" >&2; exit 1; }
fi

echo "Mounting $dmg ..."
hdiutil attach -nobrowse -noautoopen -mountpoint "$mount_point" "$dmg" >/dev/null
app_src="$(find "$mount_point" -maxdepth 1 -name '*.app' -print -quit)"
[[ -n "$app_src" ]] || { echo "No .app bundle found inside the DMG." >&2; exit 1; }

# A running app cannot be replaced cleanly; quit it first.
if pgrep -x "$APP_BIN" >/dev/null; then
    echo "Quitting the running $APP_BIN ..."
    pkill -x "$APP_BIN"
    sleep 1
fi

if [[ ! -d "$dest_dir" ]]; then
    mkdir -p "$dest_dir"
fi
if [[ ! -w "$dest_dir" ]]; then
    echo "Not writable: $dest_dir — retry with: sudo $0 -f $dmg" >&2
    echo "(The downloaded copy is temporary and is removed on exit.)" >&2
    exit 1
fi

if [[ -e "$app_path" ]]; then
    echo "Removing previous $app_path ..."
    rm -rf "$app_path"
fi

echo "Copying $APP_NAME into $dest_dir ..."
ditto "$app_src" "$app_path"

# Safety net: even a curl-downloaded dmg can pick up a quarantine flag if it
# passes through a browser once — strip it, then prove the signature is OK.
xattr -dr com.apple.quarantine "$app_path" 2>/dev/null || true
codesign --verify --deep --strict "$app_path"
echo "Signature verified (ad-hoc, no notarization required)."

echo ""
echo "Installed: $app_path"
echo "Launch it now with: open \"$app_path\""
