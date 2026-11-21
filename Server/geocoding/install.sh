#!/usr/bin/env bash
# Image build step: install Open-Meteo's geocoding API from its release .deb.
#
# The .deb is unpacked rather than installed, so its systemd hooks never run
# inside the container. The release tag is written to /etc/geocoding-release,
# and the entrypoint downloads the matching database on first start.
#
# Usage: install.sh [release tag or "latest"]

set -euo pipefail
export LC_ALL=C

REPO="https://github.com/open-meteo/geocoding-api"
TAG="${1:-latest}"

missing=()
for cmd in curl dpkg-deb mktemp ldd grep; do
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

work="$(mktemp -d "${TMPDIR:-/tmp}/geocoding.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT

deb="geocoding-api_${TAG}_jammy_amd64.deb"
curl -fL --retry 3 -o "$work/$deb" "$REPO/releases/download/$TAG/$deb"
dpkg-deb -x "$work/$deb" /

if [ ! -x /usr/local/bin/geocoding-api ]; then
    printf 'install.sh: the package did not contain /usr/local/bin/geocoding-api\n' >&2
    exit 1
fi
if ldd /usr/local/bin/geocoding-api | grep -q 'not found'; then
    printf 'install.sh: geocoding-api is missing libraries:\n' >&2
    ldd /usr/local/bin/geocoding-api | grep 'not found' >&2
    exit 1
fi

printf '%s\n' "$TAG" >/etc/geocoding-release
printf 'install.sh: geocoding-api %s installed\n' "$TAG"
