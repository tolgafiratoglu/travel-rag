#!/usr/bin/env bash
# Pull Ollama models required by travel-rag (Qwen 8B + 1.7B + nomic-embed-text).
#
# Run from project root (host), with Docker services up:
#   ./scripts/init-ollama.sh
#
# Optional env:
#   OLLAMA_CONTAINER=travel-rag-ollama
#   QWEN8B=qwen3:8b
#   QWEN1B=qwen3:1.7b-q4_K_M
#   OLLAMA_EMBED_MODEL=nomic-embed-text

set -euo pipefail

CONTAINER="${OLLAMA_CONTAINER:-travel-rag-ollama}"
QWEN8B="${QWEN8B:-qwen3:8b}"
QWEN1B="${QWEN1B:-qwen3:1.7b-q4_K_M}"
EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR: Container '$CONTAINER' is not running."
  echo "Start services first: docker compose up -d"
  exit 1
fi

echo "=== Ollama model init ==="
echo "Container: $CONTAINER"
echo "Qwen 8B:   $QWEN8B"
echo "Qwen 1.7B: $QWEN1B"
echo "Embedding: $EMBED_MODEL"
echo

echo "Pulling $QWEN8B (this may take a while on first run)..."
docker exec "$CONTAINER" ollama pull "$QWEN8B"

echo
echo "Pulling $QWEN1B..."
docker exec "$CONTAINER" ollama pull "$QWEN1B"

echo
echo "Pulling $EMBED_MODEL..."
docker exec "$CONTAINER" ollama pull "$EMBED_MODEL"

echo
echo "Installed models:"
docker exec "$CONTAINER" ollama list

echo
echo "Done."
