#!/bin/sh
# Create the default namespace once the frontend serves requests; the container healthcheck
# only probes the port.
set -eu

attempt=1
until temporal operator cluster health >/dev/null 2>&1; do
    [ "$attempt" -lt 60 ] || { echo "temporal frontend is not healthy" >&2; exit 1; }
    attempt=$((attempt + 1))
    sleep 1
done

temporal operator namespace describe --namespace default >/dev/null 2>&1 \
    || temporal operator namespace create --namespace default
