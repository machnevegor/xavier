# syntax=docker/dockerfile:1.29

# Image of the Computer: Xvfb, openbox with a wallpaper and a tint2 dock, Chromium, x11vnc and
# computerd. The desktop's defaults live in the image, not in the persistent home. Python
# libraries are pinned with hashes in requirements.txt: `uv pip compile requirements.in
# --universal --python-version 3.14 --generate-hashes --no-header -o requirements.txt`.
FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates chromium fonts-dejavu-core fonts-liberation hsetroot libxdo3 nodejs \
        openbox pcmanfm procps socat tar tint2 x11vnc xauth xdotool xterm xvfb zstd \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=bind,source=requirements.txt,target=/tmp/requirements.txt \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
RUN groupadd --gid 1000 user \
    && useradd --uid 1000 --gid 1000 --home-dir /home/user --create-home --shell /bin/bash user \
    && mkdir /workspace && chown 1000:1000 /workspace
COPY chromium-policy.json /etc/chromium/policies/managed/xavier.json
COPY applications/ /usr/local/share/applications/
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY computerd.py snapshot.js terminal.svg tint2rc wallpaper.png /opt/xavier/
ENV HOME=/home/user \
    DISPLAY=:0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 1000:1000
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
