# dRastic Backup Backend

Flask backend for the `drastic-backup` API.

## Development

```bash
uv sync
make run
```

Configuration is loaded from root-level `.env.*` files via `DRASTIC_*` variables.

## Quality checks

```bash
make lint
make format
```
