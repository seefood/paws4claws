# PAWS v1 Design Spec

**Date:** 2026-05-18
**Scope:** v1 — argv forwarding, stdout/stderr return, per-client tokens, service allowlist,
file-I/O guard

______________________________________________________________________

## Problem

Some AI agent containers may need AWS access. Injecting credentials into the container exposes them
to LLM context, tool outputs, and prompt-injection attacks.

Nano-claw pioneered the use of OneCLI for that, but the AWS API uses a stronger request signature methodology that does not support proxying it.

One possible solution is an MCP service, but raw AWS output
is often too large to pass to the LLM unfiltered; agents should be able to pipe output
through `jq`, `grep`, etc. before it reaches context and wastes tokens.

The solution: a CLI that actually wraps the requester call to the CLI, and teleports it to a sidecar container that IS exposed to the credentials.

______________________________________________________________________

## Repository Layout

```
paws4claws/
├── daemon/
│   ├── paws.py          # entire daemon — single file, stdlib only
│   └── Dockerfile       # python:3.12-slim + AWS CLI v2 official installer
├── wrapper/
│   └── aws              # drop-in shell script for agent containers
├── examples/
│   └── nanoclaw/
│       └── paws-aws.md  # Claude Code skill for nanoclaw agents
├── specs/
│   └── v1/
│       └── 2026-05-18-paws-v1-design.md
├── tests/
│   ├── unit/
│   └── integration/
├── scripts/
│   └── smoke.sh
├── .env.example
├── DESIGN.md
└── README.md
```

______________________________________________________________________

## Architecture

```
Docker network: paws-net
─────────────────────────────────────────────────────────────────────
  agent-container-A          agent-container-B
  ─────────────────          ─────────────────
  /usr/local/bin/aws         /usr/local/bin/aws
  (wrapper script)           (wrapper script)
        │                          │
        └──────────────────────────┘
                       │  POST /invoke
                       │  Authorization: Bearer <PAWS_TOKEN_*>
                       ▼
              ┌──────────────────────┐
              │   paws daemon        │
              │   (paws-net only)    │
              │                      │
              │  ThreadingHTTPServer │
              │  port 7142           │
              │                      │
              │  - verify token      │
              │  - sanitize argv     │
              │  - check allowlist   │
              │  - file-I/O guard    │
              │  - exec aws <args>   │
              │  - return JSON       │
              │                      │
              │  AWS credentials     │
              │  in environment      │
              └──────────────────────┘
```

**Daemon:** Python 3.12, `http.server.ThreadingHTTPServer`, zero Python package dependencies.
One thread per request; each thread calls `subprocess.run` and blocks until the AWS CLI v2
binary returns. Published as `ghcr.io/seefood/paws4claws`.

**Wrapper:** Shell script (`curl` + `jq`). Installed at `/usr/local/bin/aws` in agent
containers. The agent calls it exactly as it would the real CLI.

______________________________________________________________________

## Components

### `daemon/paws.py`

Single-file Python daemon. Responsibilities on startup:

1. Load all env vars matching `PAWS_TOKEN_<name>` into a set. Exit non-zero if none found.
1. Confirm `aws` binary is on PATH. Exit non-zero if not.
1. Start `ThreadingHTTPServer` on `0.0.0.0:7142`.

Routes:

- `GET /health` — no auth, returns `{"ok": true}`
- `POST /invoke` — main execution path (see Request Lifecycle below)

### `daemon/Dockerfile`

```dockerfile
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws \
    && rm -rf /var/lib/apt/lists/*
COPY paws.py /usr/local/bin/paws.py
EXPOSE 7142
HEALTHCHECK CMD curl -sf http://localhost:7142/health || exit 1
ENTRYPOINT ["python", "/usr/local/bin/paws.py"]
```

AWS CLI v2 is installed via the official bundled installer — not pip. `paws.py` has no
Python package dependencies, so no package manager is needed in this image.

Image published to `ghcr.io/seefood/paws4claws` via GitHub Actions on tag push.

### `wrapper/aws`

```sh
#!/bin/sh
PAWS_URL="${PAWS_URL:-http://paws:7142}"

ARGS=$(printf '%s\n' "$@" | jq -R . | jq -sc .)

RESPONSE=$(curl -s \
  -H "Authorization: Bearer $PAWS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"args\": $ARGS}" \
  "$PAWS_URL/invoke")

if [ $? -ne 0 ] || [ -z "$RESPONSE" ]; then
  echo "paws: daemon unreachable" >&2
  exit 1
fi

# Error responses (4xx/5xx) have an "error" field but no exitCode/stdout/stderr
if printf '%s' "$RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
  printf '%s' "$RESPONSE" | jq -r '.message // .error' >&2
  exit 1
fi

printf '%s' "$RESPONSE" | jq -r '.stdout'
printf '%s' "$RESPONSE" | jq -r '.stderr' >&2
exit "$(printf '%s' "$RESPONSE" | jq -r '.exitCode')"
```

`PAWS_URL` defaults to `http://paws:7142` (the daemon's container name on `paws-net`).
`PAWS_TOKEN` is injected at container startup — never baked into the image.
Dependencies: `curl`, `jq` — standard in any Linux base image.

______________________________________________________________________

## Request Lifecycle

```
wrapper                            daemon thread T
───────                            ────────────────
curl POST /invoke
  {"args": ["s3", "ls", ...]}
                          ──────►  parse JSON body
                                   extract Bearer token
                                   look up in token set         → 401 if missing
                                   validate args non-empty      → 400 if empty
                                   args[0] in service allowlist → 403 if not
                                   each arg passes char filter  → 403 if fail
                                   file-I/O guard (see below)   → 501 if triggered
                                   subprocess.run(["aws"]+args,
                                     shell=False,
                                     capture_output=True,
                                     timeout=120)
                          ◄──────  {"exitCode", "stdout", "stderr"}
write stdout → stdout
write stderr → stderr
exit exitCode
```

**subprocess constraints:**

- `shell=False` — argv passed directly to `execvp`, shell never sees args
- `timeout=120s` — hard ceiling per request; timed-out calls return exitCode 1
- `capture_output=True` — stdout and stderr fully buffered before response (v1; streaming is future)
- Output size cap: 10 MB per response field. If either stdout or stderr exceeds the cap,
  return exitCode 1 with a truncation notice in stderr.

______________________________________________________________________

## Sanitization Pipeline

Applied in order before subprocess. Any failure short-circuits with the appropriate error.

### 1. Service allowlist

`args[0]` must be in the configured allowlist. Default:

```
s3, ec2, logs, ssm, sts, iam, lambda, cloudformation, ecr, secretsmanager
```

Configurable via `PAWS_ALLOWED_SERVICES` (comma-separated env var).

Set `PAWS_ALLOWED_SERVICES=all` to disable the allowlist entirely and rely solely on
IAM permissions. This is appropriate when the IAM policy is already tightly scoped
and the proxy allowlist would only add friction.

IAM policy is always the primary security control; the allowlist is defense-in-depth.

Response on failure: HTTP 403, `{"error": "forbidden", "message": "paws: service 'X' is not permitted"}`

### 2. Per-arg character filter

Every argument must match `^[A-Za-z0-9:/_\-\.@=,*+%~]+$`.

Explicit block list (belt-and-suspenders): `$`, `` ` ``, `$(`, `;`, `|`, `&`, `<`, `>`,
`(`, `)`, `\`, newline, NUL, `..` (path traversal).

Response on failure: HTTP 403, `{"error": "forbidden", "message": "paws: argument rejected: 'X'"}`

### 3. File-I/O guard

Applies only when `args[0] == "s3"` and `args[1]` is one of `cp`, `mv`, `sync`.

For each positional path argument — args after the subcommand that do not start with
`--` — if the value does not start with `s3://` and is not `-` (stdout sentinel), the
request is rejected.

Response on failure: HTTP 501,

```json
{
  "error": "not_implemented",
  "message": "paws: local file I/O is not supported in v1. Only S3-to-S3 and S3-to-stdout transfers are allowed. See https://github.com/seefood/paws4claws for the roadmap."
}
```

**Allowed examples:**

- `aws s3 cp s3://bucket/key -` — S3 → stdout ✅
- `aws s3 cp s3://b/k1 s3://b/k2` — S3 → S3 ✅
- `aws s3 mv s3://b/k1 s3://b/k2` — S3 → S3 ✅

**Blocked examples:**

- `aws s3 cp s3://bucket/key /tmp/file` — local dest ❌
- `aws s3 cp /tmp/file s3://bucket/key` — local source ❌
- `aws s3 sync ./local s3://bucket/prefix` — local source ❌

______________________________________________________________________

## Authentication

The daemon loads all env vars matching `PAWS_TOKEN_<LABEL>` at startup into a set.
The `<LABEL>` is used only for logging — each agent group gets a unique token:

```bash
# .env or docker-compose environment block
PAWS_TOKEN_AGENT_A=<openssl rand -hex 32>
PAWS_TOKEN_AGENT_B=<openssl rand -hex 32>
```

All tokens authorize the same IAM credentials in v1. The wrapper sends
`Authorization: Bearer $PAWS_TOKEN` where `PAWS_TOKEN` is a single token injected
into each agent container.

A daemon with no `PAWS_TOKEN_*` vars configured exits at startup.

______________________________________________________________________

## Network Isolation

The daemon container joins `paws-net`, a dedicated Docker bridge network. It binds
to `0.0.0.0:7142` inside the container — Docker ensures only containers on `paws-net`
can reach it. Agent containers needing AWS access are added to `paws-net` at runtime.

______________________________________________________________________

## Error Response Taxonomy

| Condition                 | HTTP          | exitCode     | Body                                                                                      |
| ------------------------- | ------------- | ------------ | ----------------------------------------------------------------------------------------- |
| Missing/invalid token     | 401           | —            | `{"error": "unauthorized"}`                                                               |
| Malformed/empty body      | 400           | —            | `{"error": "bad_request", "message": "..."}`                                              |
| Service not in allowlist  | 403           | —            | `{"error": "forbidden", "message": "paws: service 'X' is not permitted"}`                 |
| Arg fails char filter     | 403           | —            | `{"error": "forbidden", "message": "paws: argument rejected: 'X'"}`                       |
| File I/O attempt          | 501           | —            | `{"error": "not_implemented", "message": "paws: local file I/O is not supported in v1…"}` |
| Command timed out         | 200           | 1            | `{"exitCode": 1, "stdout": "", "stderr": "paws: command timed out after 120s"}`           |
| Output exceeds 10 MB      | 200           | 1            | `{"exitCode": 1, "stdout": "", "stderr": "paws: output truncated — exceeds 10 MB limit"}` |
| aws CLI missing in daemon | 200           | 1            | `{"exitCode": 1, "stdout": "", "stderr": "paws: aws CLI not found in daemon container"}`  |
| Command ran               | 200           | aws exitCode | `{"exitCode": N, "stdout": "...", "stderr": "..."}`                                       |
| Daemon unreachable        | *(curl fail)* | 1            | `paws: daemon unreachable` *(printed by wrapper)*                                         |

______________________________________________________________________

## Testing

### Unit tests (`tests/unit/`)

No network, no Docker, no real AWS. Test individual functions in `paws.py`:

- Arg char filter: valid args pass; shell metacharacters, path traversal, NUL rejected
- Service allowlist: known services pass; unknown services fail; custom `PAWS_ALLOWED_SERVICES` respected; `PAWS_ALLOWED_SERVICES=all` bypasses the check entirely
- File-I/O guard: S3 URIs and `-` pass for cp/mv/sync; local paths fail; other subcommands unaffected
- Token loading: `PAWS_TOKEN_*` vars loaded correctly; no tokens → startup failure
- Response serialisation: stdout/stderr/exitCode encoded and decoded correctly

### Integration tests (`tests/integration/`)

Spin up the daemon in-process (no Docker) with `subprocess.run` mocked to return
canned `CompletedProcess` objects. Make real HTTP requests against a live socket:

- `GET /health` → 200 `{"ok": true}` (no auth required)
- Valid token + valid args → 200 with mocked aws output
- Invalid token → 401
- Unknown service → 403
- Arg with shell metacharacter → 403
- `s3 cp` with local path → 501
- `s3 cp s3://b/k -` → 200 (allowed)
- Timeout simulation → 200 exitCode 1 + timeout message
- Output > 10 MB → 200 exitCode 1 + truncation message

### Manual smoke test (`scripts/smoke.sh`)

Run locally against a real daemon container. Requires `PAWS_TOKEN` set and daemon
running on `localhost:7142`. Exercises a real `aws sts get-caller-identity` call.

______________________________________________________________________

## Nanoclaw Integration Skill (`examples/nanoclaw/paws-aws.md`)

A Claude Code skill file shipped in this repo for nanoclaw agents to consume. Intended
to be submitted as a PR to the nanoclaw skills directory once v1 is stable.

### Purpose

Teaches a nanoclaw agent how to use the `aws` proxy wrapper: what's available, what's
not, and idiomatic patterns for keeping AWS output out of LLM context.

### Skill content outline

```
---
name: paws-aws
description: Use AWS CLI via the PAWS proxy — credential-isolated aws calls for nanoclaw agents
---

## Prerequisites
- PAWS_TOKEN env var is set in this container
- PAWS_URL defaults to http://paws:7142 (override if needed)
- aws wrapper is at /usr/local/bin/aws (installed in this image)

## Usage
Use `aws` exactly as you would the real CLI. Output lands on stdout/stderr as normal;
pipe it before it reaches your context:

    aws s3 ls s3://my-bucket/prefix/ | grep ".gz" | head -20
    aws sts get-caller-identity | jq '.Account'
    aws logs describe-log-groups --query 'logGroups[*].logGroupName' --output text

## v1 Limitations
Local file I/O is not supported. These will return a 501 error:
- aws s3 cp s3://bucket/key /local/path
- aws s3 cp /local/path s3://bucket/key
- aws s3 sync ./local s3://bucket/

Allowed: S3-to-S3 and S3-to-stdout transfers:
    aws s3 cp s3://bucket/key -          # streams to stdout
    aws s3 cp s3://b/src s3://b/dst      # server-side copy

## Error handling
Non-zero exit codes are AWS errors (check stderr). If the proxy itself errors,
stderr will start with "paws:" — this is a proxy/config issue, not an AWS error.
```

The actual skill file is written verbatim to `examples/nanoclaw/paws-aws.md` and
follows the superpowers skill front-matter format so it can be dropped into any
nanoclaw agent image's skills directory.

______________________________________________________________________

## Out of Scope (v1)

- Stdin passthrough — v2
- File passing — v3
- Multiple IAM profiles mapped per token — future
- Streaming output — future
- Rate limiting — future
- Audit log beyond CloudTrail — future
