# dRastic Backup Frontend

Quasar frontend for the `drastic-backup` web UI.

## Install dependencies

```bash
corepack enable
corepack prepare yarn@4.10.3 --activate
yarn install
```

## Development

```bash
yarn dev
```

The dev server uses `DRASTIC_BIND_DEV_FRONTEND_PORT` and proxies API requests to
`QUASAR_API_PROXY_TARGET`.

## Quality checks

```bash
yarn lint
yarn format
```

## Production build

```bash
yarn build
```
