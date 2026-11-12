# Two-stage build on the hardened UBI9 Python 3.12 bases (tvoboril/base-images),
# pinned by digest. Re-pinned automatically: a base-images workflow_dispatch opens
# a signed auto/base-bump PR here with the fresh digests, the same way the melkite
# apps and cw-neuro-trainer get their RH errata.
#
# Stage 1 carries the wheel toolchain and installs into a virtualenv; stage 2 is
# the minimal runtime and only receives the finished venv.
FROM registry.voboril.xyz/base-docker/python312-build@sha256:248df396b669934fa9cf346f48e9c300a702e0fbf644ee0a8e0a826205f1a3f3 AS build
USER 0
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
COPY src ./src
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .

FROM registry.voboril.xyz/base-docker/python312-runtime@sha256:1324d171223b14c1e37f95949ba57f7f1040a53cee3ba24cc4bbb622bf472ee0
USER 0
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/youtube_ics.sqlite \
    PATH="/opt/venv/bin:$PATH"

COPY --from=build /opt/venv /opt/venv

# SQLite state lives on a mounted volume so it survives restarts. The UBI base runs
# uid 1001/gid 0; the mount point has to be writable by it.
RUN mkdir -p /data && chown -R 1001:0 /data && chmod -R g+rwX /data
VOLUME ["/data"]

USER 1001

# Long-running loop: reconcile, then sleep until 15 min before the next event.
CMD ["youtube-ics", "run"]
