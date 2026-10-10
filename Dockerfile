# syntax=docker/dockerfile:1

FROM python:3.13-slim-bookworm AS version-build
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ARG DRASTIC_VERSION
ARG DRASTIC_REVISION
RUN --mount=type=bind,target=/source \
    if [ -n "$DRASTIC_VERSION" ]; then \
        printf '%s\n' "$DRASTIC_VERSION" > /build-version.txt; \
        printf '%s\n' "$DRASTIC_REVISION" > /build-revision.txt; \
    else python /source/libs/python/common/src/drastic_common/version.py --source /source \
        --output /build-version.txt --revision-output /build-revision.txt; fi

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
COPY --from=version-build /build-version.txt /build-version.txt
RUN DRASTIC_VERSION="$(cat /build-version.txt)" yarn build

FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends restic \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY libs/python/common /libs/python/common
COPY --from=version-build /build-version.txt /libs/python/common/src/drastic_common/build-version.txt
COPY --from=version-build /build-revision.txt /libs/python/common/src/drastic_common/build-revision.txt
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/README.md ./
RUN sed -i 's|../../libs/python/common|/libs/python/common|g' pyproject.toml uv.lock
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/backend/ ./
COPY scripts/install-drastic-agent.sh src/drastic_server/services/agent/install.sh
COPY scripts/drastic-agent-installer.py src/drastic_server/services/agent/install.py
RUN sed -i 's|../../libs/python/common|/libs/python/common|g' pyproject.toml uv.lock
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/storage /app/import /app/data
RUN chmod +x /app/scripts/run.sh

COPY --from=frontend-build /app/dist/spa /app/spa
COPY --from=docs-build /app/site /app/docs-site

EXPOSE 5050

CMD ["sh", "/app/scripts/run.sh"]
