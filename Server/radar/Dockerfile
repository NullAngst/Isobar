# Isobar Server MRMS fallback radar: GDAL's official image plus one script.
# Untagged on purpose, so it tracks the current GDAL release.
FROM ghcr.io/osgeo/gdal:ubuntu-small-latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY mrms.sh n0q-colors.txt /opt/radar/

# Fail the image build, not the operator's box, if GDAL ever drops a tool the script needs.
RUN bash -c 'for c in gdalwarp gdaldem curl gzip getconf; do command -v "$c" >/dev/null || { printf "missing %s\n" "$c" >&2; exit 1; }; done; \
    command -v gdal2tiles >/dev/null || command -v gdal2tiles.py >/dev/null || { printf "missing gdal2tiles\n" >&2; exit 1; }'

# Called through bash, since a web UI upload drops the execute bit.
CMD ["bash", "/opt/radar/mrms.sh"]
