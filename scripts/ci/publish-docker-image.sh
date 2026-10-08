#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 <image-name> <dockerfile> <context>" >&2
    exit 2
fi

IMAGE_NAME=$1
DOCKERFILE=$2
CONTEXT=$3

REGISTRY=${CONTAINER_REGISTRY:?Set CONTAINER_REGISTRY.}
NAMESPACE=${CONTAINER_IMAGE_NAMESPACE:-${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required.}}
USERNAME=${CONTAINER_USERNAME:-${GITHUB_ACTOR:-}}
PASSWORD=${CONTAINER_PASSWORD:-${GITHUB_TOKEN:-${FORGEJO_TOKEN:-}}}

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
    echo "ABBRUCH: Container registry credentials missing." >&2
    echo "Set CONTAINER_USERNAME/CONTAINER_PASSWORD secrets or provide GITHUB_TOKEN/FORGEJO_TOKEN." >&2
    exit 1
fi

REGISTRY=${REGISTRY#http://}
REGISTRY=${REGISTRY#https://}
NAMESPACE=$(printf '%s' "$NAMESPACE" | tr '[:upper:]' '[:lower:]')
IMAGE="$REGISTRY/$NAMESPACE/$IMAGE_NAME"
VERSION=$(python3 "$CONTEXT/libs/python/common/src/drastic_common/version.py" --source "$CONTEXT")

TAGS=("$IMAGE:$VERSION")

case "${GITHUB_REF_TYPE:-}" in
    branch)
        SAFE_BRANCH=$(printf '%s' "${GITHUB_REF_NAME:?GITHUB_REF_NAME is required.}" | tr '/[:upper:]' '-[:lower:]')
        TAGS+=("$IMAGE:$SAFE_BRANCH")
        ;;
    tag)
        if [[ "$GITHUB_REF_NAME" != "$VERSION" ]]; then
            echo "Release tag $GITHUB_REF_NAME does not match source version $VERSION." >&2
            exit 1
        fi
        TAGS+=("$IMAGE:latest")
        ;;
esac

docker login "$REGISTRY" --username "$USERNAME" --password-stdin <<<"$PASSWORD"

BUILD_TAGS=()
for tag in "${TAGS[@]}"; do
    BUILD_TAGS+=("-t" "$tag")
done

BUILD_ARGS=(--build-arg "DRASTIC_VERSION=$VERSION")
if [[ "$DOCKERFILE" = apps/agent/Dockerfile ]]; then
    AGENT_REF=main
    if [[ "${GITHUB_REF_TYPE:-}" = branch ]]; then
        AGENT_REF=${GITHUB_REF_NAME:?GITHUB_REF_NAME is required.}
    fi
    BUILD_ARGS+=(--build-arg "DRASTIC_AGENT_REF=$AGENT_REF" --build-arg "DRASTIC_AGENT_COMMIT=$GITHUB_SHA")
fi

if [[ -n "${CONTAINER_PLATFORMS:-}" ]]; then
    docker buildx build \
        --platform "$CONTAINER_PLATFORMS" \
        --push \
        "${BUILD_TAGS[@]}" \
        "${BUILD_ARGS[@]}" \
        -f "$DOCKERFILE" \
        "$CONTEXT"
else
    docker build "${BUILD_TAGS[@]}" "${BUILD_ARGS[@]}" -f "$DOCKERFILE" "$CONTEXT"

    for tag in "${TAGS[@]}"; do
        docker push "$tag"
    done
fi
