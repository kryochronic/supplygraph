# SupplyGraph app image: API + built UI in one container.
# Build:  docker build -t supplygraph:dev .      (context = repo root, not backend/)
# State:  SQLite lives in /app/data; mount a volume there.

FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
# Layout must mirror the repo: db.py derives ROOT from its own path (data/, frontend/dist).
WORKDIR /app
COPY backend ./backend
# quotes (yfinance) and exchanges (nse): the company card's prices and "Fetch from NSE" (C5, D-34)
ARG EXTRAS=quotes,exchanges
RUN --mount=type=cache,target=/root/.cache/pip pip install -c backend/constraints.txt -e "./backend[${EXTRAS}]"
COPY data/seed ./data/seed
COPY --from=ui /ui/dist ./frontend/dist
# Host files are often 600/700; the runtime user (uid 10001) must be able to read them.
RUN chmod -R a+rX /app/backend /app/data/seed /app/frontend \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app/data && chown -R 10001 /app/data
USER 10001
ENV PYTHONUNBUFFERED=1 SG_DB_URL=sqlite:////app/data/supplygraph.db
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/themes', timeout=3)"
# Exec form so SIGTERM reaches uvicorn.
CMD ["disg", "serve", "--host", "0.0.0.0", "--port", "8000"]
