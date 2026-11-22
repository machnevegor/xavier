#!/bin/sh
# Install uv from the official Astral installer.
set -eu

readonly VERSION="${VERSION:-latest}"

if [ "${VERSION}" = "latest" ]; then
	url="https://astral.sh/uv/install.sh"
else
	url="https://astral.sh/uv/${VERSION}/install.sh"
fi

curl -LsSf "${url}" | UV_INSTALL_DIR=/usr/local/bin sh
