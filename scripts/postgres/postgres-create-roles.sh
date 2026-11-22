#!/bin/sh
# Create the xavier and temporal roles and the xavier database on first cluster initialization.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
    -v xavier_password="$XAVIER_DB_PASSWORD" \
    -v temporal_password="$TEMPORAL_DB_PASSWORD" <<'SQL'
CREATE ROLE xavier LOGIN PASSWORD :'xavier_password';
CREATE DATABASE xavier OWNER xavier;
CREATE ROLE temporal LOGIN CREATEDB PASSWORD :'temporal_password';
SQL
