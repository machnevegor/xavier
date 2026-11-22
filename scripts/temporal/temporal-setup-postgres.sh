#!/bin/sh
# Create or upgrade the Temporal persistence and visibility schemas; safe to rerun.
set -eu

schemas=/etc/temporal/schema/postgresql/v12

setup() {
    database=$1
    schema=$2
    set -- temporal-sql-tool --plugin postgres12 --ep postgres -p 5432 -u temporal --db "$database"
    "$@" create
    "$@" setup-schema -v 0.0
    "$@" update-schema -d "$schemas/$schema/versioned"
}

setup temporal temporal
setup temporal_visibility visibility
