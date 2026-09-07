FROM python:3.13-slim

WORKDIR /app

# FlagEmbedding/torch is huge (CUDA wheels on aarch64). Keep the image lean for
# runtime; BGE can be enabled later via a dedicated image or host install.
COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic-settings httpx \
        qdrant-client neo4j langgraph google-genai python-dotenv \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /cache/huggingface \
    && chown -R app:app /app /cache/huggingface

COPY --chown=app:app app/ ./app/

ENV HF_HOME=/cache/huggingface
USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
