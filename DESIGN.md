# PAWS Design

Proxied AWS Shell — a credential-isolation daemon that lets AI agents run AWS CLI
commands without holding or seeing credentials.

Repository: `paws4claws`

## Problem

AI agent containers need AWS access. Injecting `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
into an agent container exposes them to the LLM context, tool outputs, and any
prompt-injection attack the agent encounters. IAM scope limits the blast radius but
doesn't eliminate the exposure.

Additionally, if you route AWS results through an MCP tool, the full response is
ingested into LLM context. A raw `aws s3 ls` can be hundreds of lines the model
doesn't need. We want the agent to be able to filter output through `jq`, `grep`,
etc. before it lands in context — exactly as a human would at a terminal.

## Core Idea

A dedicated PAWS daemon container holds the AWS credentials. Agent containers have
none. Inside each agent container, a drop-in shell script named `aws` intercepts
every AWS call, forwards the sanitized argv to the daemon over HTTP, and writes the
result to stdout/stderr — transparently. The agent never knows it isn't running the
real binary.

Because the wrapper is a shell script that writes to stdout/stderr normally, the
agent can pipe freely:

```sh
aws s3 ls s3://my-bucket/prefix/ | grep ".gz" | head -20
```

Only the filtered result enters LLM context.

## Architecture

```
Docker network: paws-net
─────────────────────────────────────────────────────────────────────
  agent-container-A          agent-container-B         host (optional)
  ─────────────────          ─────────────────         ──────────────
  /usr/local/bin/aws         /usr/local/bin/aws        curl / script
  (wrapper script)           (wrapper script)
        │                          │                         │
        └──────────────────────────┴─────────────────────────┘
                                   │  HTTP POST /invoke
                                   │  Authorization: Bearer <token>
                                   ▼
                          ┌─────────────────────┐
                          │   paws4claws daemon  │
                          │   (paws-net only)    │
                          │                      │
                          │  - verify token      │
                          │  - sanitize argv     │
                          │  - check allowlist   │
                          │  - exec aws <args>   │
                          │  - return JSON        │
                          │                      │
                          │  AWS credentials     │
                          │  in environment      │
                          └─────────────────────┘
```

The daemon container binds only to the Docker bridge interface for `paws-net`. It is
not reachable from the public internet or from containers not joined to `paws-net`.
Network isolation is a second layer of defense alongside token auth.

## Roadmap

### v0.1 — stdout/stderr

- Daemon container on a dedicated Docker network
- `POST /invoke` accepts `{"args": [...]}`, returns `{"exitCode", "stdout", "stderr"}`
- Per-arg sanitization, service allowlist
- Multiple client tokens, single IAM profile
- Drop-in `aws` wrapper script (shell + curl + jq)

### v0.2 — stdin passthrough

- Wrapper detects piped stdin, base64-encodes it, adds `"stdin"` to payload
- Daemon decodes and pipes to subprocess stdin
- Enables: `echo data | aws s3 cp - s3://bucket/key`
- stdin is not sanitized — it is opaque data
- 10 MB decoded stdin cap (symmetric with stdout/stderr output cap)

### v0.3 — file passing (current)

- Wrapper detects local files only on an **allowlist**: S3 `cp`/`mv`/`sync` positional paths, or `file://`/`fileb://` values after known file parameters (see [docs/aws-file-input.md](docs/aws-file-input.md))
- Encodes file content inline in a `"files"` array with `argIndex`
- Daemon materializes temp files (binary 1:1), substitutes paths in argv, cleans up after exec
- Input-only (agent → daemon) — see [docs/aws-file-input.md](docs/aws-file-input.md) for catalog and limits

### v0.4 — download / output files (planned)

- Response-side counterpart to v0.3: when argv contains a **local destination** (e.g.
  `aws s3 cp s3://bucket/key ./out.bin`), daemon captures file bytes after exec and
  returns them in the JSON response (e.g. `"outputFiles"`)
- Wrapper writes decoded bytes to the agent path with exact binary fidelity
- Likely scoped first to S3 `cp`/`mv` destination paths on the same allowlist model as uploads

### v0.5 — directory sync (planned)

- `aws s3 sync` with local directory paths — recursive enumeration, multiple files per
  request, higher size/complexity than single-file upload/download

### Future

- **Streaming output** — chunked stdout/stderr instead of full buffer (today 10 MB cap)
- **Presigned URL offload** — large transfers bypass inline base64 in JSON
- Audit log beyond CloudTrail, rate limiting beyond IAM

### Explicitly not planned

- **Multiple IAM profiles mapped to tokens** — too dangerous (one token could invoke
  the wrong profile). Need two credential sets → run **two PAWS daemon containers**
  on `paws-net`, each with its own tokens and IAM role; point agent groups at the
  appropriate `PAWS_URL` / token pair.

## Wire Protocol

### v0.1 Request

```
POST /invoke
Authorization: Bearer <PAWS_TOKEN>
Content-Type: application/json

{
  "args": ["s3", "ls", "s3://my-bucket/prefix/"]
}
```

`args` is the argv as split by the shell. `args[0]` is the AWS service name.

### v0.2 Request extension

Optional `"stdin"` field — base64-encoded raw bytes. Omitted when stdin is not piped
(backward compatible with v0.1 clients). Not sanitized.

```
POST /invoke
Authorization: Bearer <PAWS_TOKEN>
Content-Type: application/json

{
  "args": ["s3", "cp", "-", "s3://my-bucket/key"],
  "stdin": "aGVsbG8K"
}
```

Invalid base64 or stdin exceeding 10 MB decoded → `400 bad_request`.

### v0.3 Request extension

Optional `"files"` array — inline file content for local path args. Each entry:

```json
{"argIndex": 2, "content": "<base64>"}
```

- `argIndex` — 0-based index into `args`
- `content` — base64 raw bytes, opaque, not sanitized; 10 MB per file
- Daemon writes `/tmp/paws-{uuid}`, substitutes bare paths or `file://` / `fileb://` URIs
- Omitting `files` behaves as v0.2

```
POST /invoke
Authorization: Bearer <PAWS_TOKEN>
Content-Type: application/json

{
  "args": ["s3", "cp", "./app.zip", "s3://my-bucket/key"],
  "files": [{"argIndex": 2, "content": "UEsDB..."}]
}
```

For a full list of AWS CLI commands that accept file or stdin input — see
[docs/aws-file-input.md](docs/aws-file-input.md).

### Response

```json
{ "exitCode": 0, "stdout": "...", "stderr": "..." }
```

HTTP status codes:

- `401` — missing or invalid token
- `403` — allowlist violation (`"error"` field has the reason)
- `400` — malformed request
- `200` — command ran (check `exitCode` for AWS-level success/failure)

### Health check

```
GET /health          (no auth required)
→ {"ok": true}
```

## Authentication

The daemon maintains a set of accepted tokens. In v0.1 all tokens authorize the same
IAM credentials — they differ only in identity for logging purposes.

Each client (agent group, host script, etc.) gets its own token:

```bash
openssl rand -hex 32
```

Tokens are configured in the daemon via environment variable or a config file
(format TBD). A daemon with no tokens configured rejects everything.

The wrapper sends: `Authorization: Bearer <PAWS_TOKEN>` where `PAWS_TOKEN` is an
env var injected into the agent container at startup — never baked into the image.

## Network Isolation

The daemon container joins a dedicated Docker bridge network (`paws-net`). Only
containers explicitly added to `paws-net` can reach the daemon. Agent containers
that need AWS access are added to `paws-net` at runtime. The host can reach the
daemon via the bridge gateway address if needed.

The daemon does **not** listen on `0.0.0.0` — only on the `paws-net` interface.
This means even containers on the default Docker network cannot reach it.

## Sanitization

Per-arg, before exec. The daemon builds an explicit argv list and calls the
subprocess with `shell=False` / `execvp`. The shell never sees the args.

Rules applied to each arg:

- Must match `[A-Za-z0-9:/_\-\.@=,*+%~]` — reject on first violation
- Explicit block: `$`, `` ` ``, `$(`, `;`, `|`, `&`, `<`, `>`, `(`, `)`, `\`, newline, NUL
- `..` (path traversal) rejected
- `args[0]` must be in the service allowlist

These rules are intentionally conservative. A valid AWS CLI argument that gets
blocked is a signal to widen the regex deliberately, not to disable the check.

## Service Allowlist

Default: `s3`, `ec2`, `logs`, `ssm`, `sts`, `iam`, `lambda`, `cloudformation`,
`ecr`, `secretsmanager`.

Configurable via `PAWS_ALLOWED_SERVICES` (comma-separated). IAM policy on the
credentials is the primary access control; the allowlist is defense-in-depth.

## Client Wrapper (`aws`)

A shell script installed in agent containers at `/usr/local/bin/aws` (or wherever
it appears in PATH before the real binary — or as the only `aws` if the real CLI
is not installed):

```sh
#!/bin/sh
PAWS_URL="${PAWS_URL:-http://paws:7142}"

ARGS=$(printf '%s\n' "$@" | jq -R . | jq -sc .)

RESPONSE=$(curl -sf \
  -H "Authorization: Bearer $PAWS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"args\": $ARGS}" \
  "$PAWS_URL/invoke")

if [ $? -ne 0 ]; then
  echo "paws: daemon unreachable" >&2
  exit 1
fi

printf '%s' "$RESPONSE" | jq -j '.stdout'
printf '%s' "$RESPONSE" | jq -j '.stderr' >&2
exit "$(printf '%s' "$RESPONSE" | jq -r '.exitCode')"
```

`PAWS_URL` defaults to `http://paws:7142` — the daemon container's name on `paws-net`.
`PAWS_TOKEN` is injected at container startup. Dependencies: `curl`, `jq` — standard
in any Linux image.

## What's Not in Scope (v0.1–v0.3)

- S3 download to local path via argv → **v0.4** (workaround: `aws s3 cp s3://… - > ./local`)
- `aws s3 sync` / directory file passing → **v0.5**
- Multiple IAM profiles per token → **not planned** (use two PAWS containers)
- Streaming output → future
- Audit log beyond CloudTrail → future
- Rate limiting beyond IAM → future

## Open Questions

1. **Daemon language**: Python stdlib (zero deps, one file), Go (static binary, easy
   concurrency), or other?
1. **Token config format**: env vars (`PAWS_TOKEN_AGENT_A`, `PAWS_TOKEN_AGENT_B`),
   a flat config file, or a mounted secrets file?
