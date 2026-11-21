# Isobar Server relay.
#
# Untagged base image on purpose: it tracks the current Python. If a release
# of Python lands before one of pyIEM's dependencies ships wheels for it, the
# image build fails in GitHub Actions (never on an operator's box). Then change
# the line below to the previous version (python:3.13-slim, say) until they do.
FROM python:3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq5 because pyIEM imports psycopg, which needs the client library even
# though the relay never talks to a database.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY isobar_relay ./isobar_relay
RUN mkdir -p /data /config /tiles

EXPOSE 8787
CMD ["python", "-m", "isobar_relay"]
