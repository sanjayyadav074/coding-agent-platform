# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.11-slim

FROM python:${PYTHON_VERSION} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY packages/coding_agent /app/packages/coding_agent
COPY services/worker/requirements.txt /app/services/worker/requirements.txt
RUN pip install /app/packages/coding_agent \
 && pip install -r /app/services/worker/requirements.txt

COPY services/worker /app/services/worker

RUN groupadd --system app && useradd --system --gid app --home /app app \
    && chown -R app:app /app
USER app
WORKDIR /app/services/worker

CMD ["python", "worker.py"]
