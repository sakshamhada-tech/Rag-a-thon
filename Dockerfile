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
COPY fixora ./fixora
COPY demo_repo ./demo_repo
COPY demo_repo_hard ./demo_repo_hard
COPY README.md SPEC.md ./
COPY --from=frontend /app/frontend/dist ./frontend/dist
EXPOSE 8000
# Bind to all interfaces inside the container; put auth/TLS in front if exposed.
CMD ["sh", "-c", "uvicorn fixora.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
