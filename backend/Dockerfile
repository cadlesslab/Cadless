# API/backend image, multi-stage.
FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY cadless ./cadless
# Install into an isolated prefix we copy into the final stage.
RUN pip install --no-cache-dir --prefix=/install ".[web]"

FROM python:3.12-slim AS final
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /install /usr/local
COPY cadless ./cadless
COPY backend ./backend

# Pre-create /data owned by appuser so a fresh named volume inherits the ownership.
RUN useradd -m appuser && mkdir -p /data && chown -R appuser /app /data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
