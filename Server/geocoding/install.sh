#!/usr/bin/env bash
# Image build step: build Open-Meteo's geocoding API from source and put the
# programs in an output folder for the final image to copy.
#
# Open-Meteo only publishes the geocoder as an x86-64 .deb, so building from
# source is the one way that works on both x86-64 and ARM servers. The build
# command is the one Open-Meteo's own release workflow uses. The release tag
# goes in RELEASE in the output folder, and the entrypoint downloads the
# place database built for that release on first start.
#
# Usage: install.sh <release tag or "latest"> <output folder>
# The programs land in <output folder>/bin, the tag in <output folder>/RELEASE.

set -euo pipefail
export LC_ALL=C

REPO="https://github.com/open-meteo/geocoding-api"
TAG="${1:-latest}"
OUT="${2:-/out}"

missing=()
for cmd in curl git swift install mktemp ldd grep; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'install.sh: missing commands: %s\n' "${missing[*]}" >&2
    exit 1
fi

if [ "$TAG" = "latest" ]; then
    # GitHub redirects /releases/latest to /releases/tag/<tag>.
    final="$(curl -fsSL --retry 3 -o /dev/null -w '%{url_effective}' "$REPO/releases/latest")"
    TAG="${final##*/}"
fi
case "$TAG" in
    ''|*[!A-Za-z0-9._-]*)
        printf 'install.sh: could not work out the release tag (got %s)\n' "$TAG" >&2
        exit 1
        ;;
esac

src="$(mktemp -d "${TMPDIR:-/tmp}/geocoding.XXXXXX")"
trap 'rm -rf -- "$src"' EXIT

git clone --quiet --depth 1 --branch "$TAG" "$REPO.git" "$src"
cd "$src" || exit 1
swift package resolve
# Swift's own libraries are linked in, so the program runs on plain Ubuntu.
swift build -c release --product Run -Xswiftc -static-stdlib

mkdir -p -- "$OUT/bin"
install -m 0755 .build/release/Run "$OUT/bin/geocoding-api"
# Swift's crash reporter, which the geocoder calls to print a readable stack trace.
if [ -f /usr/libexec/swift/linux/swift-backtrace-static ]; then
    install -m 0755 /usr/libexec/swift/linux/swift-backtrace-static "$OUT/bin/swift-backtrace"
fi
if ldd "$OUT/bin/geocoding-api" | grep -q 'not found'; then
    printf 'install.sh: geocoding-api is missing libraries:\n' >&2
    ldd "$OUT/bin/geocoding-api" | grep 'not found' >&2
    exit 1
fi

printf '%s\n' "$TAG" >"$OUT/RELEASE"
printf 'install.sh: geocoding-api %s built for %s\n' "$TAG" "$(uname -m)"
