# Isobar Server place search: Open-Meteo's geocoding API in a container.
#
# Ubuntu 22.04 because that's the system the geocoder's .deb is built for.
# GEOCODER_RELEASE defaults to the newest release at build time, so each image
# build picks up the current geocoder. The place database matching it is
# downloaded on first start, onto the geo-data volume.
FROM ubuntu:jammy

ARG GEOCODER_RELEASE=latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl zstd \
 && rm -rf /var/lib/apt/lists/*

COPY install.sh entrypoint.sh /opt/geocoding/
# Called through bash, since a web UI upload drops the execute bit.
RUN bash /opt/geocoding/install.sh "$GEOCODER_RELEASE"

VOLUME /var/lib/geocoding-api/data
EXPOSE 8080
CMD ["bash", "/opt/geocoding/entrypoint.sh"]
