# syntax=docker/dockerfile:1
# The Lapin server. Settings, the database and the voice recordings live in
# /data: keep it on a volume.
#
#     docker build -t lapin-server server
#     docker run -d --name lapin -p 8090:8090 -p 8765:8765 -v lapin-data:/data lapin-server
#
# The release images also carry the Korvo firmware, for updates over the air
# (docker build --build-context firmware=korvo/dist ...). Without it the
# Firmware button of the Devices page has nothing to send.

FROM scratch AS firmware

# a static ffmpeg (decoding of music and radio streams, Opus encoding): Debian's
# package would add 450 MB of libraries
FROM mwader/static-ffmpeg:8.0.1 AS ffmpeg

FROM python:3.13-slim AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.13-slim
RUN useradd --system --uid 1000 --user-group --home-dir /data lapin \
    && mkdir /data && chown lapin:lapin /data
COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=build /venv /venv
# the server looks for the firmware in ../../korvo/dist, next to the repository layout
COPY --from=firmware / /app/korvo/dist/
COPY assistant /app/server/assistant
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/server
USER lapin
VOLUME /data
# 8090: admin web UI, 8765: device WebSocket
EXPOSE 8090 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8765), 3)"]
CMD ["python", "-m", "assistant", "--data", "/data"]
