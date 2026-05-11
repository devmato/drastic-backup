# syntax=docker/dockerfile:1

FROM python:3.13-slim-bookworm

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends restic && rm -rf /var/lib/apt/lists/*

# Install python dependencies via uv
RUN uv sync --frozen --no-dev

# Create runtime paths
RUN mkdir -p /app/storage /app/import /app/data

# Set excecution rights for run script
RUN chmod +x /app/scripts/run.sh

CMD ["sh", "/app/scripts/run.sh"]
