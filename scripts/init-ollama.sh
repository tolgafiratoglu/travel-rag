#!/usr/bin/env bash
# Pull Ollama models required by travel-rag (Qwen + nomic-embed-text).
#
# Run from project root (host), with Docker services up:
#   ./scripts/init-ollama.sh
#
# Optional env:
#   OLLAMA_CONTAINER=travel-rag-ollama
#   OLLAMA_MODEL=qwen3:8b
#   OLLAMA_EMBED_MODEL=nomic-embed-text

set -euo pipefail

CONTAINER="${OLLAMA_CONTAINER:-travel-rag-ollama}"
LLM_MODEL="${OLLAMA_MODEL:-qwen3:8b}"
EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR: Container '$CONTAINER' is not running."
  echo "Start services first: docker compose up -d"
  exit 1
fi

echo "=== Ollama model init ==="
echo "Container: $CONTAINER"
echo "LLM:       $LLM_MODEL"
echo "Embedding: $EMBED_MODEL"
echo

echo "Pulling $LLM_MODEL (this may take a while on first run)..."
docker exec "$CONTAINER" ollama pull "$LLM_MODEL"

echo
echo "Pulling $EMBED_MODEL..."
docker exec "$CONTAINER" ollama pull "$EMBED_MODEL"

echo
echo "Installed models:"
docker exec "$CONTAINER" ollama list

echo
echo "Done."
