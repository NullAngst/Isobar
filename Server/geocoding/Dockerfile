# Isobar Server place search: Open-Meteo's geocoding API in a container.
#
# Built from source in Swift's official image, for whichever CPU the image is
# built for (x86-64 or ARM), then copied onto plain Ubuntu 22.04, the system
# the geocoder's own releases target. Both images are untagged past the Ubuntu
# release, so each build gets the current Swift. GEOCODER_RELEASE defaults to
# the newest geocoder release at build time. The place database matching it
# downloads on first start, onto the geo-data volume.
FROM swift:jammy AS build

ARG GEOCODER_RELEASE=latest
# curl looks up the newest release; the Swift image ships git but not curl.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
COPY install.sh /opt/geocoding/install.sh
# Called through bash, since a web UI upload drops the execute bit.
RUN bash /opt/geocoding/install.sh "$GEOCODER_RELEASE" /out


FROM ubuntu:jammy

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl zstd \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /out/bin/ /usr/local/bin/
COPY --from=build /out/RELEASE /etc/geocoding-release
COPY entrypoint.sh /opt/geocoding/
# The build checked libraries against the Swift image; check again here, on
# the system that actually runs it.
RUN if ldd /usr/local/bin/geocoding-api | grep 'not found'; then \
      printf 'geocoding-api is missing libraries on plain Ubuntu\n' >&2; exit 1; \
    fi

VOLUME /var/lib/geocoding-api/data
EXPOSE 8080
CMD ["bash", "/opt/geocoding/entrypoint.sh"]
