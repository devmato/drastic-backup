#!/usr/bin/env bash
# Promote an already-built image pair after approval; never rebuild a candidate.
set -euo pipefail

TAG=${1:?Pass the release tag.}
if [[ $# -gt 2 || ( $# -eq 2 && "$2" != --advance-main ) ]]; then
    echo "Usage: $0 <tag> [--advance-main]" >&2
    exit 1
fi
REGISTRY=${CONTAINER_REGISTRY:?Set CONTAINER_REGISTRY.}
NAMESPACE=${CONTAINER_IMAGE_NAMESPACE:-${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required.}}
USERNAME=${CONTAINER_USERNAME:-${GITHUB_ACTOR:-}}
PASSWORD=${CONTAINER_PASSWORD:-${GITHUB_TOKEN:-${FORGEJO_TOKEN:-}}}

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
    echo "Container registry credentials missing." >&2
    exit 1
fi
REGISTRY=${REGISTRY#http://}
REGISTRY=${REGISTRY#https://}
NAMESPACE=$(printf '%s' "$NAMESPACE" | tr '[:upper:]' '[:lower:]')

# Retrying an older run must never move latest back to an earlier release.
test "$TAG" = "$(python3 libs/python/common/src/drastic_common/version.py)"
test "$(git rev-parse "refs/tags/$TAG^{commit}")" = "$(git rev-parse HEAD)"
remote_main=$(git ls-remote origin refs/heads/main)
if [[ "${2:-}" == --advance-main ]]; then
    if [[ -n "$remote_main" ]]; then
        git fetch origin refs/heads/main
        git merge-base --is-ancestor FETCH_HEAD HEAD
    fi
else
    test "${remote_main%%[[:space:]]*}" = "$(git rev-parse HEAD)"
fi

docker login "$REGISTRY" --username "$USERNAME" --password-stdin <<<"$PASSWORD"
for image in drastic-backup-server drastic-backup-agent; do
    docker buildx imagetools inspect "$REGISTRY/$NAMESPACE/$image:$TAG"
done
if [[ "${2:-}" == --advance-main ]]; then
    # A concurrent main update rejects this push instead of overwriting it.
    git push origin HEAD:refs/heads/main
fi
# Check the entire pair before changing either alias. A partial promotion can be retried.
for image in drastic-backup-server drastic-backup-agent; do
    docker buildx imagetools create --tag "$REGISTRY/$NAMESPACE/$image:latest" "$REGISTRY/$NAMESPACE/$image:$TAG"
done
