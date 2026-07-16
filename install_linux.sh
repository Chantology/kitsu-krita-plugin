#!/bin/bash
# Installs the Kitsu Krita Plugin into Krita's pykrita resource folder on Linux.
#
# Usage: ./install_linux.sh [custom pykrita folder]
#   Krita on Linux can be installed several ways (native package, Flatpak,
#   Snap), each with its own resource folder. This script guesses which one
#   you have installed; if it guesses wrong, find your actual folder via
#   Krita's Settings > Manage Resources > Open Resource Folder and pass its
#   "pykrita" subfolder as an argument to this script instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/pykrita"

XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"

if [ "${1:-}" != "" ]; then
    TARGET="$1"
    GUESSED=0
elif [ -d "$HOME/.var/app/org.kde.krita" ]; then
    TARGET="$HOME/.var/app/org.kde.krita/data/krita/pykrita"     # Flatpak
    GUESSED=1
elif [ -d "$HOME/snap/krita" ]; then
    TARGET="$HOME/snap/krita/current/.local/share/krita/pykrita" # Snap
    GUESSED=1
else
    TARGET="$XDG_DATA_HOME/krita/pykrita"                        # Native package
    GUESSED=1
fi

echo "Installing Kitsu Krita Plugin"
echo "  From: $SOURCE"
echo "  To:   $TARGET"
echo
if [ "$GUESSED" = "1" ]; then
    echo "(Guessed based on what's installed. If this is wrong, find your"
    echo " actual pykrita folder via Krita's Settings > Manage Resources >"
    echo " Open Resource Folder and re-run this script with that path as an"
    echo " argument.)"
    echo
fi

if [ ! -f "$SOURCE/kitsu_krita_plugin.desktop" ]; then
    echo "ERROR: $SOURCE/kitsu_krita_plugin.desktop not found."
    echo "Run this script from the repository root, or check your checkout."
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
