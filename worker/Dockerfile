# Execution-worker image: OCCT/build123d toolchain + worker service.
FROM python:3.12-slim

# System libs required by cadquery-ocp / OCCT (OpenGL + X11 runtime deps), plus
# the fonts `Text()` needs.
#
# OCCT resolves a typeface through fontconfig. With no system font installed it
# does not fail -- it falls back to the single-line CAD face build123d bundles
# and writes a warning to stderr, so a script that asks for a family gets a
# different one and the geometry still comes out. That is the wrong failure for
# a catalogue: an engraved part would be authored in one face here and rendered
# in another anywhere a font happens to exist. Installing one family makes the
# default deterministic and makes a requested name actually resolve.
#
# fontconfig substitutes rather than raising, so an unavailable family is still
# silently replaced by the closest match -- name a family that is installed.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglu1-mesa libxrender1 libxext6 libsm6 libx11-6 \
      fontconfig fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer first (cached across app-code changes) — OCP is the heavy part.
COPY pyproject.toml README.md constraints.txt ./
COPY cadless ./cadless
# -c pins the exact resolution instead of re-resolving on every build, so an
# image built today holds the same versions as one built next month. Without it
# the build takes whatever each range permits on the day, which is how a major
# SDK release reached an image nobody had changed.
RUN pip install --no-cache-dir -c constraints.txt ".[web]"

# App code for the worker service.
COPY worker ./worker

# Pre-create /data owned by appuser so a fresh named volume inherits the ownership.
RUN useradd -m appuser && mkdir -p /data && chown -R appuser /app /data
USER appuser

EXPOSE 9000
CMD ["uvicorn", "worker.service:app", "--host", "0.0.0.0", "--port", "9000"]
