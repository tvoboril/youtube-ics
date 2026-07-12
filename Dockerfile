FROM python:3.12-slim

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
