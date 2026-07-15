#!/usr/bin/env bash
# Create/refresh the host .venv for offline scripts (parse/ingest).
# API runtime deps live in the Docker image (api/Dockerfile); this mirrors that
# list plus script-only pymysql.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if [[ "${1:-}" == "--fresh" ]]; then
  echo "Removing existing ${VENV_DIR}..."
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating ${VENV_DIR} with ${PYTHON}..."
  "$PYTHON" -m venv "$VENV_DIR"
else
  echo "Using existing ${VENV_DIR}"
fi

# Keep in sync with api/Dockerfile pip install line (+ pymysql for scripts)
echo "Installing host dependencies..."
"${VENV_DIR}/bin/pip" install -U pip
"${VENV_DIR}/bin/pip" install \
  fastapi \
  "uvicorn[standard]" \
  pydantic-settings \
  httpx \
  qdrant-client \
  neo4j \
  langgraph \
  google-genai \
  python-dotenv \
  pymysql

echo "Verifying imports..."
"${VENV_DIR}/bin/python" -c "import neo4j, pymysql, google.genai; print('ok')"

echo
echo "Done. Example:"
echo "  MARIADB_HOST=localhost NEO4J_URI=bolt://localhost:7687 LLM_PROVIDER=gemini \\"
echo "  ${VENV_DIR}/bin/python scripts/ingest-wikivoyage-neo4j.py --page Bordeaux --dry-run"
