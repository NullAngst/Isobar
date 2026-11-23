# Isobar Server relay.
#
# Untagged base image on purpose: it tracks the current Python. A brand new
# Python often lands before every library ships prebuilt wheels for it (pyIEM
# needs pygrib, which had none for 3.15 at first), so the build installs a
# compiler and the eccodes library pygrib builds against. Anything without a
# wheel gets compiled instead of failing the build. The compiler is removed
# again afterwards; the eccodes library stays, since pygrib loads it at run time.
FROM python:3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ECCODES_DIR=/usr

WORKDIR /app
COPY requirements.txt .

# libpq5 because pyIEM imports psycopg, which needs the client library even
# though the relay never talks to a database.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 libeccodes-dev gcc g++ \
 && pip install -r requirements.txt \
 && apt-get purge -y --auto-remove gcc g++ \
 && rm -rf /var/lib/apt/lists/*

COPY isobar_relay ./isobar_relay
RUN mkdir -p /data /config /tiles

EXPOSE 8787
CMD ["python", "-m", "isobar_relay"]
