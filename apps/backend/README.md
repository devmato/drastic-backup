# dRastic Backup Backend

Flask backend for the `drastic-backup` API.

## Development

From the repository root, use `./scripts/dev.sh up`. See [Development Setup](../../docs/development.md) for configuration and database migrations.

For backend-only checks, run from this directory:

```bash
uv sync --frozen --dev
uv run ruff check src tests
uv run pytest
```

The application reads `DRASTIC_*` process environment variables. Compose and the development helper load env files; `make run` does not load the repository's `.env.*` files itself. See [Configuration](../../docs/configuration.md).
