# Base image pinned by digest (SLSA hardening — ADR-0007 Phase C). Bump via:
#   docker buildx imagetools inspect python:3.12-slim
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/youtube_ics.sqlite

# Install the package (console script `youtube-ics`) and its deps.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# SQLite state lives on a mounted volume so it survives restarts.
VOLUME ["/data"]

# Long-running loop: reconcile, then sleep until 15 min before the next event.
CMD ["youtube-ics", "run"]
