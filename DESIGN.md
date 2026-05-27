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

### v1 — stdout/stderr

- Daemon container on a dedicated Docker network
- `POST /invoke` accepts `{"args": [...]}`, returns `{"exitCode", "stdout", "stderr"}`
- Per-arg sanitization, service allowlist
- Multiple client tokens, single IAM profile
- Drop-in `aws` wrapper script (shell + curl + jq)

### v2 — stdin passthrough (current)

- Wrapper detects piped stdin, base64-encodes it, adds `"stdin"` to payload
- Daemon decodes and pipes to subprocess stdin
- Enables: `echo data | aws s3 cp - s3://bucket/key`
- stdin is not sanitized — it is opaque data
- 10 MB decoded stdin cap (symmetric with stdout/stderr output cap)

### v3 — file passing (tentative)

- Wrapper detects args that are existing local files or `file://` URIs
- Encodes file content inline in a `"files"` array with `argIndex`
- Daemon materializes temp files, substitutes paths in argv, cleans up after exec
- Open questions: distinguishing file paths from S3 keys/log group names, size limits

### Future

- Multiple IAM profiles mapped to specific tokens (token → profile lookup)
- Presigned URL offload for large file transfers
- Streaming output (chunked response)

## Wire Protocol

### v1 Request

```
POST /invoke
Authorization: Bearer <PAWS_TOKEN>
Content-Type: application/json

{
  "args": ["s3", "ls", "s3://my-bucket/prefix/"]
}
```

`args` is the argv as split by the shell. `args[0]` is the AWS service name.

### v2 Request extension

Optional `"stdin"` field — base64-encoded raw bytes. Omitted when stdin is not piped
(backward compatible with v1 clients). Not sanitized.

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

### v1 Response

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

The daemon maintains a set of accepted tokens. In v1 all tokens authorize the same
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

printf '%s' "$RESPONSE" | jq -r '.stdout'
printf '%s' "$RESPONSE" | jq -r '.stderr' >&2
exit "$(printf '%s' "$RESPONSE" | jq -r '.exitCode')"
```

`PAWS_URL` defaults to `http://paws:7142` — the daemon container's name on `paws-net`.
`PAWS_TOKEN` is injected at container startup. Dependencies: `curl`, `jq` — standard
in any Linux image.

## What's Not in Scope (v1–v2)

- File passing — v3
- Multiple IAM profiles — future
- Streaming output — future
- Audit log beyond CloudTrail — future
- Rate limiting beyond IAM — future

## Open Questions

1. **Daemon language**: Python stdlib (zero deps, one file), Go (static binary, easy
   concurrency), or other?
1. **Token config format**: env vars (`PAWS_TOKEN_AGENT_A`, `PAWS_TOKEN_AGENT_B`),
   a flat config file, or a mounted secrets file?
1. **v3 file detection heuristic**: `[ -f "$arg" ]` is simple but could false-positive
   on paths that happen to exist on the wrapper host. Worth a stricter heuristic?
