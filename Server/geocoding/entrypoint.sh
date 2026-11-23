#!/usr/bin/env bash
# Starts the geocoder. On first start, or after an image update brings a new
# geocoder release, it first downloads the database built for that release
# (about 500 MB compressed), checks its SHA-256, unpacks it, runs the
# geocoder's own verify-database on it, and only then swaps it in. The
# database lives on the geo-data volume, so later starts skip all of that.

set -euo pipefail
export LC_ALL=C

REPO="https://github.com/open-meteo/geocoding-api"
HOME_DIR="/var/lib/geocoding-api"
DATA="$HOME_DIR/data"
BIN="/usr/local/bin/geocoding-api"

missing=()
for cmd in curl zstd sha256sum mktemp; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'geocoding: missing commands: %s\n' "${missing[*]}" >&2
    exit 1
fi

TAG="$(cat /etc/geocoding-release)"
mkdir -p -- "$DATA"
cd "$HOME_DIR" || exit 1

have=""
if [ -f "$DATA/release" ]; then
    have="$(cat "$DATA/release")"
fi

if [ ! -f "$DATA/database-v1.bin" ] || [ "$have" != "$TAG" ]; then
    printf 'geocoding: downloading the place database for release %s\n' "$TAG"
    work="$(mktemp -d "$DATA/download.XXXXXX")"
    trap 'rm -rf -- "$work"' EXIT
    url="$REPO/releases/download/$TAG"
    curl -fL --retry 3 -o "$work/database-v1.bin.zst" "$url/database-v1.bin.zst"
    curl -fL --retry 3 -o "$work/database-v1.bin.zst.sha256" "$url/database-v1.bin.zst.sha256"
    (cd "$work" || exit 1; sha256sum --check database-v1.bin.zst.sha256)
    printf 'geocoding: unpacking the database\n'
    zstd -q -f -d "$work/database-v1.bin.zst" -o "$DATA/database-v1.bin.new"
    rm -f -- "$work/database-v1.bin.zst"
    chmod 644 "$DATA/database-v1.bin.new"
    printf 'geocoding: unpacked to %s MB. Checking every record before using it.\n' \
        "$(( $(wc -c <"$DATA/database-v1.bin.new") / 1048576 ))"
    printf 'geocoding: this is one CPU core working through the whole file, with no output until it ends. Place search answers once it is done.\n'
    started=$SECONDS
    "$BIN" verify-database --database "$DATA/database-v1.bin.new"
    printf 'geocoding: check passed in %s seconds\n' "$(( SECONDS - started ))"
    mv -- "$DATA/database-v1.bin.new" "$DATA/database-v1.bin"
    printf '%s\n' "$TAG" >"$DATA/release"
    rm -rf -- "$work"
    trap - EXIT
    printf 'geocoding: database ready\n'
fi

printf 'geocoding: serving place search on port 8080\n'

exec "$BIN" serve -b 0.0.0.0:8080 --env production
