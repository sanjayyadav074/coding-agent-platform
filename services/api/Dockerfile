# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.11-slim

FROM python:${PYTHON_VERSION} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# Install only what we need to compile wheels, then drop it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install the shared library first to maximise layer caching.
COPY packages/coding_agent /app/packages/coding_agent
COPY services/api/requirements.txt /app/services/api/requirements.txt
RUN pip install /app/packages/coding_agent \
 && pip install -r /app/services/api/requirements.txt

# App source
COPY services/api /app/services/api

# Drop to non-root.
RUN groupadd --system app && useradd --system --gid app --home /app app \
    && chown -R app:app /app
USER app
WORKDIR /app/services/api

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read(); sys.exit(0)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
