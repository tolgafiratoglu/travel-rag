#!/usr/bin/env bash
# Import Wikivoyage SQL table dumps into MariaDB.
#
# Dumps: https://dumps.wikimedia.org/enwikivoyage/latest/
# Place .sql.gz files under data/wikivoyage/ (mounted at /data/wikivoyage in the container).
#
# Article wikitext lives in enwikivoyage-latest-pages-articles.xml.bz2 — that file is NOT
# loaded into MariaDB; it will be consumed by the ingestion pipeline (chunk → embed → Qdrant).
#
# Run from project root (host), with MariaDB up:
#   ./scripts/import-wikivoyage.sh
#
# Force re-import (drops tables via dump SQL):
#   ALLOW_DB_RESET=true ./scripts/import-wikivoyage.sh
#
# Optional env:
#   MARIADB_CONTAINER=travel-rag-mariadb
#   MARIADB_DATABASE=wikivoyage
#   MARIADB_USER=wikivoyage
#   MARIADB_PASSWORD=changeme
#   DUMP_DIR=/data/wikivoyage

set -euo pipefail

CONTAINER="${MARIADB_CONTAINER:-travel-rag-mariadb}"
DB="${MARIADB_DATABASE:-wikivoyage}"
USER="${MARIADB_USER:-wikivoyage}"
PASS="${MARIADB_PASSWORD:-changeme}"
DUMP_DIR="${DUMP_DIR:-/data/wikivoyage}"
ALLOW_DB_RESET="${ALLOW_DB_RESET:-false}"

SQL_DUMPS=(
  enwikivoyage-latest-linktarget.sql.gz
  enwikivoyage-latest-page.sql.gz
  enwikivoyage-latest-categorylinks.sql.gz
)

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR: Container '$CONTAINER' is not running."
  echo "Start services first: docker compose up -d mariadb"
  exit 1
fi

mariadb_exec() {
  docker exec -i "$CONTAINER" mariadb -u"$USER" -p"$PASS" "$DB" "$@"
}

import_sql() {
  local filename="$1"
  local path="$DUMP_DIR/$filename"
  echo "Importing $filename ..."
  docker exec -i "$CONTAINER" bash -c "gunzip -c '$path' | mariadb -u'$USER' -p'$PASS' '$DB'"
}

echo "=== Wikivoyage MariaDB import ==="
echo "Container: $CONTAINER"
echo "Database:  $DB"
echo "Dump dir:  $DUMP_DIR"

if ! docker exec "$CONTAINER" test -d "$DUMP_DIR"; then
  echo "ERROR: Dump directory '$DUMP_DIR' not found in container."
  exit 1
fi

for filename in "${SQL_DUMPS[@]}"; do
  if ! docker exec "$CONTAINER" test -f "$DUMP_DIR/$filename"; then
    echo "ERROR: Missing dump file: $DUMP_DIR/$filename"
    exit 1
  fi
done

if docker exec "$CONTAINER" test -f "$DUMP_DIR/enwikivoyage-latest-pages-articles.xml.bz2"; then
  echo "Note: pages-articles.xml.bz2 present (content dump — not imported to MariaDB)."
else
  echo "Note: pages-articles.xml.bz2 not found; add it for wikitext ingestion later."
fi

existing_pages="$(mariadb_exec -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB' AND table_name='page';" 2>/dev/null || echo 0)"
if [[ "$existing_pages" == "1" ]]; then
  row_count="$(mariadb_exec -N -e 'SELECT COUNT(*) FROM page;' 2>/dev/null || echo 0)"
  if [[ "$row_count" -gt 0 && "$ALLOW_DB_RESET" != "true" ]]; then
    echo "SKIP: page table already has $row_count rows."
    echo "Set ALLOW_DB_RESET=true to re-import (dump SQL drops and recreates tables)."
    exit 0
  fi
fi

for filename in "${SQL_DUMPS[@]}"; do
  import_sql "$filename"
done

page_count="$(mariadb_exec -N -e 'SELECT COUNT(*) FROM page WHERE page_namespace = 0;')"
echo "Done. Main-namespace pages: $page_count"
