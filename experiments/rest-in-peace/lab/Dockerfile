# The REST in Peace lab: dashboard + every experiment (J1-J5) in one image.
# Build:  docker compose -f docker-compose-lab.yml build      (context = repo root)
# The experiments folder and reports/ are mounted at run time, so code edits and
# results show up on the host without a rebuild.

FROM python:3.12-slim
# pdftotext is J1's second-opinion extractor
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*
# Layout mirrors the repo: common.py derives ROOT from its own path.
WORKDIR /app
COPY backend ./backend
COPY mcp-server ./mcp-server
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c backend/constraints.txt -e ./backend -e "./mcp-server[dev]"
COPY data/seed ./data/seed
COPY experiments/rest-in-peace ./experiments/rest-in-peace
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && chmod -R a+rX /app && mkdir -p /app/experiments/rest-in-peace/out && chown -R 10001 /app/experiments
USER 10001
ENV PYTHONUNBUFFERED=1 LAB_HOST=0.0.0.0 LAB_PORT=58200
EXPOSE 58200
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:58200/api/status', timeout=3)"
CMD ["python", "experiments/rest-in-peace/lab/server.py"]
