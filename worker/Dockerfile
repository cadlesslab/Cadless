# Execution-worker image: OCCT/build123d toolchain + worker service.
FROM python:3.12-slim

# System libs required by cadquery-ocp / OCCT (OpenGL + X11 runtime deps).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglu1-mesa libxrender1 libxext6 libsm6 libx11-6 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer first (cached across app-code changes) — OCP is the heavy part.
COPY pyproject.toml README.md ./
COPY cadless ./cadless
RUN pip install --no-cache-dir ".[web]"

# App code for the worker service.
COPY worker ./worker

# Pre-create /data owned by appuser so a fresh named volume inherits the ownership.
RUN useradd -m appuser && mkdir -p /data && chown -R appuser /app /data
USER appuser

EXPOSE 9000
CMD ["uvicorn", "worker.service:app", "--host", "0.0.0.0", "--port", "9000"]
