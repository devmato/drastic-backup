# Building the Documentation Site

The documentation is written as Markdown files in `docs/` and rendered with [MkDocs](https://www.mkdocs.org/) using the [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) theme.

## Prerequisites

- Python 3.11 or later.
- `pip` or `uv` for package management.

## Installation

Install MkDocs Material locally:

```bash
pip install mkdocs-material
```

Or with `uv`:

```bash
uv pip install mkdocs-material
```

## Local Preview

Start a local documentation server with live reload:

```bash
mkdocs serve
```

The site is available at `http://127.0.0.1:8000`.

## Build Static Site

Generate static HTML:

```bash
mkdocs build --site-dir docs-site
```

The output is written to `docs-site/`. This directory is generated output and should not be committed.

## Project Configuration

The MkDocs configuration lives in `mkdocs.yml` in the repository root.

It defines:

- Site metadata.
- Navigation order.
- Material theme settings.
- Markdown extensions.
- Excluded internal files such as `docs/changelog/UNRELEASED.md`.

## Adding a Page

1. Create a Markdown file in `docs/`.
2. Add it to the `nav` section in `mkdocs.yml`.
3. Run `mkdocs serve` or `mkdocs build` to verify links and rendering.

## Integrated Serving

The backend serves the built documentation site at `/docs/` when `DOCS_SITE_PATH` points to a built MkDocs directory.

Production behavior:

- The root `Dockerfile` builds docs in a dedicated `docs-build` stage.
- The generated site is copied into the runtime image at `/app/docs-site`.

Development behavior:

- `docker-compose.dev.yaml` builds docs into `/tmp/drastic-docs-site` before backend startup.
- `DRASTIC_DOCS_SITE_PATH` points the backend to that generated directory.
- The frontend dev server proxies `/docs` to the backend.

Local behavior without Docker:

- Run `mkdocs build --site-dir docs-site` from the repository root.
- Start the backend with `DRASTIC_DOCS_SITE_PATH=../../docs-site` or another matching path.
