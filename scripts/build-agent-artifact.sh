#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VERSION=${1:-$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")}
VERSION=${VERSION#v}
ARTIFACT_OS="linux"
ARTIFACT_ARCH="amd64"
ARTIFACT_NAME="drastic-agent-${ARTIFACT_OS}-${ARTIFACT_ARCH}-v${VERSION}"
OUTPUT_DIR="$ROOT_DIR/dist"
STAGING_DIR="$OUTPUT_DIR/$ARTIFACT_NAME"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "ABBRUCH: Agent-Artefakte werden aktuell nur auf Linux gebaut." >&2
    exit 1
fi

case "$(uname -m)" in
    x86_64|amd64)
        ;;
    *)
        echo "ABBRUCH: Agent-Artefakte werden aktuell nur fuer linux amd64 gebaut." >&2
        exit 1
        ;;
esac

cd "$ROOT_DIR/apps/agent"
uv sync --frozen --group build
uv run --group build pyinstaller \
    --clean \
    --onefile \
    --name drastic-agent \
    --workpath build/pyinstaller \
    --specpath build/pyinstaller \
    --paths src \
    --paths "$ROOT_DIR/libs/python/common/src" \
    src/drastic_agent/__main__.py

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"
cp dist/drastic-agent "$STAGING_DIR/drastic-agent"
cp README.md "$STAGING_DIR/README.md"
printf '%s\n' "$VERSION" > "$STAGING_DIR/VERSION"
cat > "$STAGING_DIR/USAGE.md" <<'EOF'
# dRastic Backup Agent

Run the agent with the required `DRASTIC_*` environment variables configured for your server.

```bash
./drastic-agent --help
```
EOF

mkdir -p "$OUTPUT_DIR"
tar -C "$OUTPUT_DIR" -czf "$OUTPUT_DIR/$ARTIFACT_NAME.tar.gz" "$ARTIFACT_NAME"
echo "$OUTPUT_DIR/$ARTIFACT_NAME.tar.gz"
