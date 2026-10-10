# syntax=docker/dockerfile:1

FROM python:3.11-slim-bookworm AS version-build
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ARG DRASTIC_VERSION
ARG DRASTIC_REVISION
RUN --mount=type=bind,target=/source \
    if [ -n "$DRASTIC_VERSION" ]; then \
        printf '%s\n' "$DRASTIC_VERSION" > /build-version.txt; \
        printf '%s\n' "$DRASTIC_REVISION" > /build-revision.txt; \
    else python /source/libs/python/common/src/drastic_common/version.py --source /source \
        --output /build-version.txt --revision-output /build-revision.txt; fi

FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /opt/drastic-agent/tools/bin/uv

ARG DRASTIC_AGENT_GIT_REPOSITORY=https://github.com/devmato/drastic-backup.git

ENV PATH="/opt/drastic-agent/tools/bin:${PATH}" \
    UV_PROJECT_ENVIRONMENT=/opt/drastic-agent/releases/image/venv \
    UV_CACHE_DIR=/opt/drastic-agent/cache/uv \
    UV_PYTHON_DOWNLOADS=never \
    DRASTIC_AGENT_DEPLOYMENT=docker \
    DRASTIC_AGENT_INSTALL_SOURCE=git \
    DRASTIC_AGENT_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

COPY scripts/install-agent-dependencies.sh /opt/drastic-agent/releases/image/source/scripts/
RUN bash /opt/drastic-agent/releases/image/source/scripts/install-agent-dependencies.sh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/drastic-agent/releases/image/source/apps/agent

COPY libs/python/common ../../libs/python/common
COPY --from=version-build /build-version.txt ../../libs/python/common/src/drastic_common/build-version.txt
COPY --from=version-build /build-revision.txt ../../libs/python/common/src/drastic_common/build-revision.txt
COPY apps/agent/pyproject.toml apps/agent/uv.lock apps/agent/README.md ./

# Install python dependencies via uv
RUN uv sync --frozen --no-dev --no-editable --no-install-project

COPY apps/agent/ ./
RUN uv sync --frozen --no-dev --no-editable

COPY scripts/drastic-agent-installer.py ../../scripts/
RUN python ../../scripts/drastic-agent-installer.py image \
    --repository "$DRASTIC_AGENT_GIT_REPOSITORY"

WORKDIR /app
COPY apps/agent/run.sh /app/run.sh
CMD ["sh", "/app/run.sh"]
