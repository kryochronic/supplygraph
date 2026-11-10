# Build:  docker build -t sg-exchange-mcp:dev mcp-server
# Stateless image: no volume, configuration from environment only.

FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.13-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /install /usr/local
USER 10001
ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
# Exec form so SIGTERM reaches uvicorn and in-flight requests drain on pod shutdown.
CMD ["sg-exchange-mcp"]
