#!/usr/bin/env bash
# Wraps the PyInstaller build in dist/Isobar into a single-file AppImage.
#
# Run it from anywhere after `pyinstaller --noconfirm --clean isobar.spec`:
#   packaging/appimage/build-appimage.sh v1.5.0
# The argument is only the label in the file name. The result lands in the
# repo root as Isobar-<label>-linux-x86_64.AppImage, plus a .zsync file that
# lets AppImageUpdate (and Gear Lever and the like) fetch just what changed.
#
# appimagetool is downloaded from its "continuous" release unless APPIMAGETOOL
# points at a copy you already have.

set -euo pipefail

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

label="${1:-}"
[ -n "$label" ] || die "usage: $0 <version label, like v1.5.0>"

missing=()
for tool in cp install ln mkdir rm chmod; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ -z "${APPIMAGETOOL:-}" ]; then
  command -v curl >/dev/null 2>&1 || missing+=("curl")
fi
if [ "${#missing[@]}" -gt 0 ]; then
  die "missing commands: ${missing[*]}"
fi

# A branch name like feature/x would put a slash in the file name.
label="${label//\//-}"

here="$(cd "$(dirname -- "$0")" && pwd)" || exit 1
root="$(cd "$here/../.." && pwd)" || exit 1
dist="$root/dist/Isobar"
work="$root/build/appimage"
appdir="$work/Isobar.AppDir"
name="Isobar-$label-linux-x86_64.AppImage"
id="io.github.nullangst.isobar"

[ -x "$dist/Isobar" ] || die "No PyInstaller build at $dist. Run: pyinstaller --noconfirm --clean isobar.spec"

rm -rf -- "$appdir"
mkdir -p -- "$appdir/usr/lib" "$appdir/usr/share/applications" \
  "$appdir/usr/share/icons/hicolor/256x256/apps" "$appdir/usr/share/icons/hicolor/1024x1024/apps"

# The program, its _internal folder and everything in it, kept together.
cp -R -- "$dist" "$appdir/usr/lib/isobar"

install -m 755 -- "$here/AppRun" "$appdir/AppRun"
install -m 644 -- "$here/$id.desktop" "$appdir/$id.desktop"
install -m 644 -- "$here/$id.desktop" "$appdir/usr/share/applications/$id.desktop"
install -m 644 -- "$root/isobar/web/icon.png" "$appdir/usr/share/icons/hicolor/256x256/apps/$id.png"
install -m 644 -- "$root/assets/icon.png" "$appdir/usr/share/icons/hicolor/1024x1024/apps/$id.png"
install -m 644 -- "$root/isobar/web/icon.png" "$appdir/$id.png"
ln -sf -- "$id.png" "$appdir/.DirIcon"

tool="${APPIMAGETOOL:-$work/appimagetool}"
if [ ! -x "$tool" ]; then
  mkdir -p -- "$work"
  curl -fsSL -o "$tool" \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x -- "$tool"
fi

# Lets update tools find newer builds on the GitHub releases page.
update="gh-releases-zsync|NullAngst|Isobar|latest|Isobar-*-linux-x86_64.AppImage.zsync"

# APPIMAGE_EXTRACT_AND_RUN: build machines (and containers) often have no FUSE,
# and appimagetool is an AppImage itself.
cd "$root" || exit 1
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream -u "$update" "$appdir" "$name"

printf 'Built %s\n' "$root/$name"
