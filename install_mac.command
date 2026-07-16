#!/bin/bash
# Installs the Kitsu Krita Plugin into Krita's pykrita resource folder on macOS.
#
# Usage: ./install_mac.command [custom pykrita folder]
#   Double-click to run with no arguments, installing to the standard
#   location (~/Library/Application Support/krita/pykrita). If your Krita
#   install uses a different resource folder, find it via Krita's
#   Settings > Manage Resources > Open Resource Folder, then pass its
#   "pykrita" subfolder as an argument to this script instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/pykrita"

if [ "${1:-}" != "" ]; then
    TARGET="$1"
else
    TARGET="$HOME/Library/Application Support/krita/pykrita"
fi

echo "Installing Kitsu Krita Plugin"
echo "  From: $SOURCE"
echo "  To:   $TARGET"
echo

if [ ! -f "$SOURCE/kitsu_krita_plugin.desktop" ]; then
    echo "ERROR: $SOURCE/kitsu_krita_plugin.desktop not found."
    echo "Run this script from the repository root, or check your checkout."
    read -r -p "Press Enter to close this window..." _
    exit 1
fi

mkdir -p "$TARGET"

# Remove any previous install of this plugin's own folder first, for a
# clean, idempotent install that never leaves stale files from an older
# version behind.
rm -rf "$TARGET/kitsu_krita_plugin"

cp "$SOURCE/kitsu_krita_plugin.desktop" "$TARGET/kitsu_krita_plugin.desktop"
cp -R "$SOURCE/kitsu_krita_plugin" "$TARGET/kitsu_krita_plugin"

echo "Done. Restart Krita, then enable \"Kitsu Krita Plugin\" in"
echo "Settings > Configure Krita > Python Plugin Manager."
read -r -p "Press Enter to close this window..." _
