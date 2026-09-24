# syntax=docker/dockerfile:1

FROM python:3.11-slim-bookworm

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Update package lists and install openssh-client for ssh-keygen command
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*

COPY libs/python/common /libs/python/common
COPY apps/agent/pyproject.toml apps/agent/uv.lock apps/agent/README.md ./
RUN sed -i 's|../../libs/python/common|/libs/python/common|g' pyproject.toml uv.lock

# Install python dependencies via uv
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/agent/ ./
RUN sed -i 's|../../libs/python/common|/libs/python/common|g' pyproject.toml uv.lock
RUN uv sync --frozen --no-dev

# Set excecution rights for run script
RUN chmod +x /app/run.sh

# Set start script
CMD ["sh", "/app/run.sh"]
