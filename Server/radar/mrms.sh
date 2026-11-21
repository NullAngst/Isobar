#!/usr/bin/env bash
# Builds Isobar Server's fallback radar tiles from NOAA MRMS.
#
# Every 30 seconds it looks for a new CONUS "reflectivity at lowest altitude"
# file in NOAA's public MRMS bucket (a new one lands every 2 minutes). Each new
# file is unzipped, warped to Web Mercator, colored with IEM's own N0Q
# reflectivity colors (so Isobar can repaint it with any palette, the same as
# IEM radar) and cut into XYZ tiles, zoom 3 to 8, in a folder named for its
# scan time. The newest 12 frames are kept, and index.json lists them for the
# relay. Everything is done with GDAL's own tools.
#
# Settings (environment):
#   ENABLE_RADAR   true or false. false: idle, write nothing.
#   TILES_DIR      where the frame folders and index.json go (default /tiles)
#   RADAR_ZOOMS    gdal2tiles zoom range (default 3-8). Drop to 3-7 if one
#                  frame takes longer than 2 minutes to render.
#   RADAR_KEEP     frames to keep (default 12, which is 24 minutes)

set -euo pipefail
export LC_ALL=C

BUCKET="https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT="CONUS/ReflectivityAtLowestAltitude_00.50"
TILES="${TILES_DIR:-/tiles}"
ZOOMS="${RADAR_ZOOMS:-3-8}"
KEEP="${RADAR_KEEP:-12}"
CHECK_EVERY=30
COLORS="${RADAR_COLORS:-/opt/radar/n0q-colors.txt}"

log() {
    printf '%s mrms: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

fail() {
    printf '%s mrms: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >&2
}

check_commands() {
    local missing=() cmd
    for cmd in curl gzip gdalwarp gdaldem date mktemp tr sed tail mv rm chmod getconf; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if command -v gdal2tiles >/dev/null 2>&1; then
        GDAL2TILES="$(command -v gdal2tiles)"
    elif command -v gdal2tiles.py >/dev/null 2>&1; then
        GDAL2TILES="$(command -v gdal2tiles.py)"
    else
        missing+=("gdal2tiles")
    fi
    if [ "${#missing[@]}" -gt 0 ]; then
        fail "missing commands: ${missing[*]}"
        exit 1
    fi
}

enabled() {
    case "${ENABLE_RADAR:-true}" in
        1|true|TRUE|True|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

# Keys newer than $2 under the prefix $1, oldest first, one per line.
list_keys() {
    local prefix="$1" after="$2" url
    url="$BUCKET/?list-type=2&prefix=$prefix"
    if [ -n "$after" ]; then
        url="$url&start-after=$after"
    fi
    # S3 answers XML. Splitting on "<" puts each <Key> on its own line.
    curl -fsS --max-time 30 "$url" | tr '<' '\n' | sed -n 's/^Key>//p'
}

# The newest key in today's (UTC) folder. Just after midnight the new folder
# is empty for a couple of minutes, which only means no new frame yet.
newest_key() {
    local after="$1" today listing
    today="$(date -u '+%Y%m%d')"
    listing="$(list_keys "$PRODUCT/$today/" "$after")" || return 1
    printf '%s\n' "$listing" | tail -n 1
}

# MRMS_ReflectivityAtLowestAltitude_00.50_20261120-183805.grib2.gz -> 20261120-183805
frame_id() {
    local name="${1##*/}"
    name="${name%.grib2.gz}"
    printf '%s\n' "${name##*_}"
}

render() {
    local key="$1" id="$2" work out
    work="$(mktemp -d "${TMPDIR:-/tmp}/mrms.XXXXXX")"
    out="$TILES/.building-$id"
    rm -rf -- "$out"
    if ! curl -fsS --retry 2 --max-time 120 -o "$work/in.grib2.gz" "$BUCKET/$key"; then
        rm -rf -- "$work"
        return 1
    fi
    if ! gzip -dc "$work/in.grib2.gz" >"$work/in.grib2" \
        || ! gdalwarp -q -overwrite -t_srs EPSG:3857 -r near -dstnodata -999 -co COMPRESS=DEFLATE \
            "$work/in.grib2" "$work/merc.tif" \
        || ! gdaldem color-relief -q -alpha -nearest_color_entry "$work/merc.tif" "$COLORS" "$work/color.tif" \
        || ! "$GDAL2TILES" --xyz -q -z "$ZOOMS" -r near -w none --processes="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')" \
            "$work/color.tif" "$out" >/dev/null; then
        rm -rf -- "$work" "$out"
        return 1
    fi
    rm -rf -- "$work"
    rm -rf -- "${TILES:?}/$id"
    mv -- "$out" "$TILES/$id"
}

# Drop all but the newest $KEEP frames and rewrite index.json.
write_index() {
    local frames=() dir id t tmp i first=1
    for dir in "$TILES"/[0-9]*-[0-9]*; do
        [ -d "$dir" ] || continue
        frames+=("${dir##*/}")
    done
    local count="${#frames[@]}"
    if [ "$count" -gt "$KEEP" ]; then
        for (( i = 0; i < count - KEEP; i++ )); do
            rm -rf -- "${TILES:?}/${frames[i]}"
        done
        frames=("${frames[@]:count-KEEP}")
    fi
    tmp="$(mktemp "$TILES/.index.XXXXXX")"
    {
        printf '{"updated": "%s", "frames": [' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        for id in "${frames[@]}"; do
            t="${id:0:4}-${id:4:2}-${id:6:2}T${id:9:2}:${id:11:2}:${id:13:2}Z"
            if [ "$first" -eq 1 ]; then first=0; else printf ', '; fi
            printf '{"id": "%s", "time": "%s"}' "$id" "$t"
        done
        printf ']}\n'
    } >"$tmp"
    chmod 644 "$tmp"
    mv -- "$tmp" "$TILES/index.json"
}

main() {
    check_commands
    mkdir -p -- "$TILES"
    rm -rf -- "$TILES"/.building-* "$TILES"/.index.*
    local last="" key id started took
    if ! enabled; then
        log "ENABLE_RADAR is off; idling"
    fi
    while true; do
        if ! enabled; then
            sleep 60
            continue
        fi
        if ! key="$(newest_key "$last")"; then
            fail "could not list $BUCKET/$PRODUCT"
            sleep "$CHECK_EVERY"
            continue
        fi
        if [ -n "$key" ] && [ "$key" != "$last" ]; then
            id="$(frame_id "$key")"
            if [ -d "$TILES/$id" ]; then
                last="$key"
            else
                started="$(date -u '+%s')"
                if render "$key" "$id"; then
                    last="$key"
                    write_index
                    took=$(( $(date -u '+%s') - started ))
                    log "frame $id ready in ${took}s"
                    if [ "$took" -gt 110 ]; then
                        log "rendering takes longer than the 2 minute MRMS cycle; set RADAR_ZOOMS=3-7"
                    fi
                else
                    fail "frame $id failed; trying again on the next check"
                fi
            fi
        fi
        sleep "$CHECK_EVERY"
    done
}

main "$@"
