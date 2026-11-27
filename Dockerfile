# Multi-stage build: Node builds the React UI, Python serves API + static UI.
FROM node:20-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Optional neural embeddings for RAG (hybrid retrieval works either way):
# fastembed loads BAAI/bge-small-en-v1.5 from Hugging Face at image build time.
# If the download or install fails, the image still ships and the app falls back
# to the local hashed bag-of-n-grams embedder (fully offline, deterministic).
RUN pip install --no-cache-dir fastembed==0.9.1 || true
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    || echo "fastembed model pre-download failed; runtime will use the hash embedder"
COPY fixora ./fixora
COPY demo_repo ./demo_repo
COPY demo_repo_hard ./demo_repo_hard
COPY demo_repo_syntax ./demo_repo_syntax
COPY demo_repo_import ./demo_repo_import
COPY README.md SPEC.md ./
RUN useradd --create-home fixora && mkdir -p /app/logs && chown -R fixora /app
USER fixora
COPY --from=frontend /app/frontend/dist ./frontend/dist
EXPOSE 8000
# Bind to all interfaces inside the container; put auth/TLS in front if exposed.
CMD ["sh", "-c", "uvicorn fixora.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
