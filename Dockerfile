# syntax=docker/dockerfile:1

FROM python:3.13-slim-bookworm AS docs-build

WORKDIR /app

COPY mkdocs.yml ./
COPY docs/ docs/
RUN pip install --no-cache-dir mkdocs-material && mkdocs build

FROM node:22-bookworm-slim AS frontend-build

WORKDIR /app

RUN corepack enable && corepack prepare yarn@4.10.3 --activate

COPY apps/frontend/package.json apps/frontend/yarn.lock apps/frontend/.yarnrc.yml ./
RUN yarn install --immutable

COPY apps/frontend/ ./
RUN yarn build

FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends restic \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY libs/python/common /libs/python/common
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/README.md ./
RUN sed -i 's|../../libs/python/common|/libs/python/common|g' pyproject.toml uv.lock
RUN uv sync --frozen --no-dev --no-install-project

COPY .env.default /app/.env.default
COPY apps/backend/ ./
RUN sed -i 's|../../libs/python/common|/libs/python/common|g' pyproject.toml uv.lock
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/storage /app/import /app/data
RUN chmod +x /app/scripts/run.sh

COPY --from=frontend-build /app/dist/spa /app/spa
COPY --from=docs-build /app/site /app/docs-site

EXPOSE 5050

CMD ["sh", "/app/scripts/run.sh"]
