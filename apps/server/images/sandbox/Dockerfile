# syntax=docker/dockerfile:1.29

# Image of Execute runs and of the workspace holder. Python libraries are pinned with hashes in
# requirements.txt: `uv pip compile requirements.in --universal --python-version 3.14
# --generate-hashes --no-header -o requirements.txt`.
FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates nodejs tar zstd \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=bind,source=requirements.txt,target=/tmp/requirements.txt \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
# Empty volumes take the owner of these directories when first mounted.
RUN groupadd --gid 1000 user \
    && useradd --uid 1000 --gid 1000 --home-dir /home/user --create-home --shell /bin/bash user \
    && mkdir /workspace && chown 1000:1000 /workspace
ENV HOME=/home/user \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 1000:1000
WORKDIR /workspace
CMD ["sleep", "infinity"]
