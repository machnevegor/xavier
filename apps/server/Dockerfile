# syntax=docker/dockerfile:1.29

# Image of the api and worker processes.

ARG PYTHON_IMAGE=python:3.14.10-slim-trixie

FROM ghcr.io/astral-sh/uv:0.9.9 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first: this layer is rebuilt only when the lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
ARG SOURCE_DATE_EPOCH
RUN groupadd --system --gid 10001 xavier \
    && useradd --system --uid 10001 --gid xavier --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin xavier
COPY --from=build /app/.venv /app/.venv
# The Harness runtime extracts native addons under PKG_NATIVE_CACHE_PATH (default ~/.cache,
# and the user has no home); /tmp of the worker must allow exec.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XAVIER_SETTINGS_FILE=/etc/xavier/settings.yaml \
    PKG_NATIVE_CACHE_PATH=/tmp/pkg-cache
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/healthz', timeout=2)"]
ENTRYPOINT ["xavier"]
CMD ["api"]
