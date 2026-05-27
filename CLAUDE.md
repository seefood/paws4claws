# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
uv run --group dev pytest

# Run a single test file
uv run --group dev pytest tests/unit/test_sanitize.py

# Run a single test by name
uv run --group dev pytest tests/integration/test_server.py::test_health_ok -v

# Lint (ruff is run automatically by the prek pre-commit hook)
uv run --group dev ruff check daemon/ tests/

# Build the daemon image locally
docker build -t paws4claws:local daemon/

# Run manual smoke test against a live daemon
PAWS_TOKEN=mytoken ./scripts/smoke.sh
```

**Important:** `requires-python = ">=3.12"` in `pyproject.toml` is required — without it, `uv` picks the system Python (3.9). If tests fail with syntax errors, delete `.venv` and re-run.

## Pre-commit hooks (prek)

The repo uses [prek](https://github.com/j178/prek) (a Rust pre-commit runner). Hooks run ruff and may auto-fix files. After a hook modifies a file, you must `git add` that file again and retry the commit — the amended commit path does **not** work here, always create a new commit.

## Architecture

**Single-file daemon** (`daemon/paws.py`, Python 3.12 stdlib only):

- `ThreadingHTTPServer` on port 7142 (configurable via `PAWS_PORT`)
- `make_handler(tokens, allowed_services)` — factory that bakes config into a `PawsHandler` subclass; used in tests to avoid patching `os.environ`
- `load_tokens(env?)` — reads `PAWS_TOKEN_<LABEL>=...` env vars into a frozenset
- `load_allowed_services(env?)` — returns `None` (all services allowed) if `PAWS_ALLOWED_SERVICES=all`, else a frozenset; defaults to 10 common services

**Sanitization pipeline** (applied in order on every `/invoke` request):

1. Service allowlist — `args[0]` must be in the allowed set (or allowlist is `None`)
1. Per-arg character filter — `validate_arg()` enforces `[A-Za-z0-9:/_\-\.@=,*+%~]+`, blocks `..`, `$(`, and a set of shell-special chars
1. File-I/O guard — `check_file_io()` returns 501 for `s3 cp/mv/sync` with local path args; S3-to-S3, S3-to-stdout (`-`), and stdin upload (`-` source + piped stdin) are allowed

**Wire protocol:**

- `POST /invoke` — `{"args": [...]}` or `{"args": [...], "stdin": "<base64>"}` → `{"exitCode": N, "stdout": "...", "stderr": "..."}`
- Optional `stdin` field: base64-encoded bytes, not sanitized; 10 MB cap; invalid base64 → 400
- `GET /health` — `{"ok": true}` (no auth)
- `401` bad/missing token · `403` allowlist or sanitize · `400` malformed · `501` file I/O · `200` always for exec (check `exitCode`)

**Wrapper** (`wrapper/aws`): POSIX shell, depends only on `curl` and `jq`. Drop into agent containers at `/usr/local/bin/aws`. Reads `PAWS_TOKEN` (required) and `PAWS_URL` (defaults to `http://paws:7142`). Detects piped stdin with `[ ! -t 0 ]`, base64-encodes it into the request payload.

**Integration tests** (`tests/integration/test_server.py`): spin up a real `ThreadingHTTPServer` on a random port (port 0) in-process via a `scope="module"` pytest fixture. `subprocess.run` is patched via `unittest.mock.patch`. No Docker required.

## Token configuration

Tokens are set as env vars on the daemon container: `PAWS_TOKEN_<LABEL>=<hex>`. Generate with `openssl rand -hex 32`. A daemon with zero token env vars refuses to start. In v1, all tokens authorize the same IAM credentials.

## CI / Publishing

`.github/workflows/publish.yml` triggers on `v*` tags and pushes to `ghcr.io/seefood/paws4claws`. Tag a release with `git tag v0.1.0 && git push origin v0.1.0`.
