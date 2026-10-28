#!/bin/sh
set -eu

: "${POSTGRES_SEEDS:?POSTGRES_SEEDS is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"

database_exists() {
  temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "${DB_PORT:-5432}" \
    --db "$1" \
    connect >/dev/null 2>&1
}

setup_database() {
  database="$1"
  schema_path="$2"

  if ! database_exists "$database"; then
    temporal-sql-tool \
      --plugin postgres12 \
      --ep "$POSTGRES_SEEDS" \
      -u "$POSTGRES_USER" \
      -p "${DB_PORT:-5432}" \
      --db "$database" \
      create
  fi

  temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "${DB_PORT:-5432}" \
    --db "$database" \
    setup-schema -v 0.0 || true

  temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "${DB_PORT:-5432}" \
    --db "$database" \
    update-schema -d "$schema_path"
}

echo "Waiting for PostgreSQL at ${POSTGRES_SEEDS}:${DB_PORT:-5432}..."
nc -z -w 10 "$POSTGRES_SEEDS" "${DB_PORT:-5432}"

setup_database temporal /etc/temporal/schema/postgresql/v12/temporal/versioned
setup_database temporal_visibility /etc/temporal/schema/postgresql/v12/visibility/versioned

echo 'Temporal PostgreSQL schemas are ready.'
