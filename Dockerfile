FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS application
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FRONTEND_DIR=/app/frontend/dist
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home adrobot \
    && mkdir -p /data && chown adrobot:adrobot /data
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY --from=frontend /build/dist/ ./frontend/dist/
USER adrobot
EXPOSE 8000
# ASGI lifespan runs Alembic before accepting traffic and aborts on failure.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
