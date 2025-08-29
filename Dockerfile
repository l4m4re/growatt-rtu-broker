# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (optional: for usb-serial stability)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Copy and install package
COPY pyproject.toml ./
COPY growatt_broker ./growatt_broker

RUN pip install --upgrade pip && \
    pip install .

# Create log dir
RUN mkdir -p /var/log

# Default command: show help
CMD ["growatt-broker", "--help"]
