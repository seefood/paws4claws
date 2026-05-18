# paws4claws — Proxied AWS Shell

A credential-isolation daemon for AI agent containers. Agents run AWS CLI commands
without ever holding credentials; a dedicated sidecar container holds them, executes
the CLI, and returns the result. The agent can pipe output through `jq`, `grep`, etc.
before it ever reaches the LLM.

## Problem

AI agent containers cannot safely hold long-lived AWS credentials:

- Credentials in the container mean credentials in the LLM's reachable environment
- Raw AWS output is often too large to pass to the LLM unfiltered
- OneCLI proxies HTTP APIs but cannot intercept local subprocess calls to `aws`

## How it works

The PAWS daemon runs in its own container on a dedicated Docker network. Agent
containers that need AWS access are added to that network. Inside each agent
container, a drop-in shell script named `aws` replaces (or precedes) the real
binary. The agent uses it exactly as it would the real CLI:

```
agent calls:  aws s3 ls s3://bucket/ | grep ".csv"
                    │
              /usr/local/bin/aws  (wrapper script — curl + jq, no credentials)
                    │  POST /invoke  {"args": ["s3", "ls", "s3://bucket/"]}
                    │  Authorization: Bearer <token>
                    ▼
              paws daemon container  (holds AWS credentials, runs aws CLI)
                    │  {"exitCode": 0, "stdout": "...", "stderr": "..."}
                    ▼
              wrapper writes stdout/stderr, exits with exitCode
                    │
              grep ".csv"   ← runs locally, only matches reach LLM context
```

The agent never has `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or any token.
Each agent container gets its own bearer token; all tokens map to the same IAM
credentials in v1.

## Roadmap

| Version | Feature                                                                     |
| ------- | --------------------------------------------------------------------------- |
| **v1**  | argv forwarding, stdout/stderr return, per-client tokens, service allowlist |
| **v2**  | stdin passthrough (`echo data \| aws s3 cp - s3://bucket/key`)              |
| **v3**  | file passing (wrapper detects local file args, inlines them in the request) |
| future  | multiple IAM profiles mapped to tokens, streaming output                    |

## Security model

- **Credentials never in the agent container.** The daemon holds them; agents
  communicate only over HTTP with a bearer token.
- **Network isolation.** The daemon binds only to the `paws-net` Docker bridge.
  Containers not joined to that network cannot reach it.
- **Per-client bearer tokens.** Each agent container gets a unique random token.
  A daemon with no tokens configured rejects everything.
- **Argv sanitization.** Every argument is validated against a strict character
  allowlist before exec. `shell=False` — the shell never sees the args.
- **Service allowlist.** Only explicitly permitted AWS services can be invoked.
- **IAM is the primary security control.** The allowlist is defense-in-depth.
  Scope the IAM credentials to exactly what the agents need.

## See also

[DESIGN.md](DESIGN.md) — full architecture, wire protocol, sanitization rules,
and open design questions.
