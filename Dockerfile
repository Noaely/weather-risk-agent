# One service: the FastAPI backend also serves the built React UI (same origin, one URL).
# Secrets (ANTHROPIC_API_KEY, ACCESS_CODE) are NOT in the image: .dockerignore excludes .env, and the
# host injects them as environment variables at runtime.

# ---- 1. build the UI ---------------------------------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. run the API + the built UI -------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
# The database snapshot the eval and docs describe (2016-2025), plus the FEMA NRI national table that
# the scoring reads for US county percentiles. No ingestion at deploy time: nothing to fail if a public
# API is down, and the answers match the documented numbers exactly.
COPY data/hubs.json data/weather.db data/
COPY data/raw/nri_counties.csv data/raw/nri_counties.meta.json data/raw/
COPY --from=ui /ui/dist frontend/dist

RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
# Render (and most hosts) pass the port in $PORT.
CMD ["sh", "-c", "python -m uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
