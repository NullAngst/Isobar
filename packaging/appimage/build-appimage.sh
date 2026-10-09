#!/usr/bin/env bash
# Wraps the PyInstaller build in dist/Isobar into a single-file AppImage.
#
# Run it from anywhere after `pyinstaller --noconfirm --clean isobar.spec`:
#   packaging/appimage/build-appimage.sh v1.4.0
# The argument is only the label in the file name. The result lands in the
# repo root as Isobar-<label>-linux-x86_64.AppImage, plus a .zsync file that
# lets AppImageUpdate (and Gear Lever and the like) fetch just what changed.
#
# appimagetool is downloaded from its "continuous" release unless APPIMAGETOOL
# points at a copy you already have.

set -euo pipefail

LABEL="${1:-}"
if [ -z "$LABEL" ]; then
  echo "usage: $0 <version label, like v1.4.0>" >&2
  exit 2
fi

# A branch name like feature/x would put a slash in the file name.
LABEL="${LABEL//\//-}"

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DIST="$ROOT/dist/Isobar"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/Isobar.AppDir"
NAME="Isobar-$LABEL-linux-x86_64.AppImage"
ID="io.github.nullangst.isobar"

if [ ! -x "$DIST/Isobar" ]; then
  echo "No PyInstaller build at $DIST. Run: pyinstaller --noconfirm --clean isobar.spec" >&2
  exit 1
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$APPDIR/usr/share/icons/hicolor/1024x1024/apps"

# The program, its _internal folder and everything in it, kept together.
cp -a "$DIST" "$APPDIR/usr/lib/isobar"

install -m 755 "$HERE/AppRun" "$APPDIR/AppRun"
install -m 644 "$HERE/$ID.desktop" "$APPDIR/$ID.desktop"
install -m 644 "$HERE/$ID.desktop" "$APPDIR/usr/share/applications/$ID.desktop"
install -m 644 "$ROOT/isobar/web/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/$ID.png"
install -m 644 "$ROOT/assets/icon.png" "$APPDIR/usr/share/icons/hicolor/1024x1024/apps/$ID.png"
install -m 644 "$ROOT/isobar/web/icon.png" "$APPDIR/$ID.png"
ln -sf "$ID.png" "$APPDIR/.DirIcon"

TOOL="${APPIMAGETOOL:-$WORK/appimagetool}"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi

# Lets update tools find newer builds on the GitHub releases page.
UPDATE="gh-releases-zsync|NullAngst|Isobar|latest|Isobar-*-linux-x86_64.AppImage.zsync"

# APPIMAGE_EXTRACT_AND_RUN: build machines (and containers) often have no FUSE,
# and appimagetool is an AppImage itself.
cd "$ROOT"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream -u "$UPDATE" "$APPDIR" "$NAME"

echo "Built $ROOT/$NAME"
