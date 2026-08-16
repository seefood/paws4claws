# Installing paws4claws

This guide covers everything needed to run PAWS in Docker — standalone or via
`docker-compose` — and how to wire agent containers into it. It is written so
that an AI agent (e.g. NanoClaw) can read it and produce its own integration
skill from scratch.

## Prerequisites

- Docker (Engine ≥ 20)
- `openssl` for token generation
- Agent containers need `curl` and `jq` (most base images include them)

______________________________________________________________________

## 1. Get the image

### From GHCR (once a release tag is pushed)

```sh
docker pull ghcr.io/seefood/paws4claws:latest
```

### Build locally (until the image is published)

```sh
git clone https://github.com/seefood/paws4claws.git
cd paws4claws
docker build -t paws4claws:local daemon/
```

Verify the build:

```sh
docker run --rm --entrypoint aws paws4claws:local --version
# aws-cli/2.x.x ...
```

Use `paws4claws:local` wherever the examples below say `ghcr.io/seefood/paws4claws:latest`.

______________________________________________________________________

## 2. Create the Docker network

The daemon and all agent containers that need AWS access must share a dedicated
bridge network. Create it once:

```sh
docker network create paws-net
```

______________________________________________________________________

## 3. Generate a bearer token

Each agent container gets its own token. All tokens authorize the same IAM
credentials in v0.1. Generate one per agent (or one per agent group):

```sh
openssl rand -hex 32
# e.g. a3f8c2d1e4b5...  (64 hex chars)
```

Keep the value — you will set it on both the daemon and the agent container.

______________________________________________________________________

## 4. Run the daemon

The daemon reads AWS credentials and bearer tokens from environment variables.

### Minimal run (IAM user credentials)

```sh
docker run -d \
  --name paws \
  --network paws-net \
  -e AWS_ACCESS_KEY_ID=AKIA... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e PAWS_TOKEN_AGENT_A=<token-from-step-3> \
  ghcr.io/seefood/paws4claws:latest
```

### With an AWS profile (shared credentials file)

```sh
docker run -d \
  --name paws \
  --network paws-net \
  -v ~/.aws:/root/.aws:ro \
  -e AWS_PROFILE=my-profile \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e PAWS_TOKEN_AGENT_A=<token> \
  ghcr.io/seefood/paws4claws:latest
```

### Using IMDSv2 (EC2 instance profile)

No credential env vars needed — the real AWS CLI inside the container will pick
up the instance metadata automatically:

```sh
docker run -d \
  --name paws \
  --network paws-net \
  -e PAWS_TOKEN_AGENT_A=<token> \
  ghcr.io/seefood/paws4claws:latest
```

### Multiple tokens (one per agent)

Add one `PAWS_TOKEN_<LABEL>` env var per agent. The label is arbitrary — it is
not sent over the wire and is only for your own bookkeeping:

```sh
-e PAWS_TOKEN_NANOCLAW=abc...
-e PAWS_TOKEN_HERMES=def...
-e PAWS_TOKEN_SCRIPT=ghi...
```

### Optional configuration

| Variable                | Default                              | Description                                                                                  |
| ----------------------- | ------------------------------------ | -------------------------------------------------------------------------------------------- |
| `PAWS_PORT`             | `7142`                               | Port the daemon listens on                                                                   |
| `PAWS_ALLOWED_SERVICES` | `s3,ec2,logs,ssm,sts,iam,lambda,`... | Comma-separated AWS services. Set to `all` to allow everything (IAM is still the real gate). |

### Health check

```sh
curl http://localhost:7142/health      # only works from paws-net or host bridge
# {"ok": true}
```

______________________________________________________________________

## 5. Wire an agent container to PAWS

Two things are required inside each agent container:

1. The `aws` wrapper script at `/usr/local/bin/aws`
1. The `PAWS_TOKEN` env var (matching one of the `PAWS_TOKEN_*` values on the daemon)

### Install the wrapper

**Nanoclaw operators:** see [`examples/nanoclaw/add-paws4claws/SKILL.md`](examples/nanoclaw/add-paws4claws/SKILL.md) for three install modes — **host `~/bin` (recommended)**, runtime read-only bind mount, or image bake-in. The steps below match **image bake-in** (nanoclaw mode A).

Copy `wrapper/aws` and `wrapper/file_allowlist.sh` from this repo into the agent image at build time:

```dockerfile
COPY --chmod=755 wrapper/file_allowlist.sh /usr/local/lib/paws/file_allowlist.sh
COPY --chmod=755 wrapper/profile_resolve.sh /usr/local/lib/paws/profile_resolve.sh
COPY --chmod=755 wrapper/aws /usr/local/bin/aws
```

Or inject it at runtime if you cannot modify the agent image:

```sh
docker cp wrapper/file_allowlist.sh <agent-container>:/usr/local/lib/paws/file_allowlist.sh
docker cp wrapper/profile_resolve.sh <agent-container>:/usr/local/lib/paws/profile_resolve.sh
docker cp wrapper/aws <agent-container>:/usr/local/bin/aws
docker exec <agent-container> chmod +x /usr/local/bin/aws
```

The wrapper requires only `curl` and `jq` — no Python, no AWS credentials.

### Connect to paws-net and set the token

```sh
docker run -d \
  --name my-agent \
  --network paws-net \
  -e PAWS_TOKEN=<same-token-as-PAWS_TOKEN_LABEL-above> \
  my-agent-image
```

The wrapper defaults `PAWS_URL` to `http://paws:7142` — Docker's DNS resolves
`paws` to the daemon container as long as both are on `paws-net` and the daemon
is named `paws`. Override with `-e PAWS_URL=http://...` if needed.

### Multiple AWS accounts (AWS_PROFILE)

To reach more than one AWS account from the same agent container, run one PAWS
daemon per account (see step 4) and set `AWS_PROFILE` in the agent before each
`aws` call. The wrapper maps `AWS_PROFILE=<name>` to the env vars
`PAWS_URL_<SUFFIX>` / `PAWS_TOKEN_<SUFFIX>`, where `<SUFFIX>` is `<name>`
uppercased with non-alphanumeric characters replaced by `_` — e.g. `acct-a`
becomes `ACCT_A`.

There is no default profile. If `AWS_PROFILE` is set but no matching
`PAWS_URL_<SUFFIX>`/`PAWS_TOKEN_<SUFFIX>` pair is configured, the wrapper fails
immediately (exit 1, `paws: ...` on stderr) without making any network call.
If `AWS_PROFILE` is unset, the wrapper falls back to the bare `PAWS_URL`/
`PAWS_TOKEN` pair — whether that fallback exists is up to you: leave
`PAWS_URL`/`PAWS_TOKEN` configured on the agent to give it a de facto default
account, or omit them to force every call to set `AWS_PROFILE` explicitly.

Assuming two daemons `paws-a` and `paws-b` are already running on `paws-net`
(one per account, per step 4 — each daemon needs its own `--name` (e.g.
`paws-a`, `paws-b`) since Docker DNS resolves that name on `paws-net`), wire
the agent to both:

```sh
docker run -d \
  --name my-agent \
  --network paws-net \
  -e PAWS_URL_ACCT_A=http://paws-a:7142 \
  -e PAWS_TOKEN_ACCT_A=<token-configured-on-paws-a> \
  -e PAWS_URL_ACCT_B=http://paws-b:7142 \
  -e PAWS_TOKEN_ACCT_B=<token-configured-on-paws-b> \
  my-agent-image
```

Inside the agent container:

```sh
AWS_PROFILE=acct-a aws s3 ls   # routed to paws-a
AWS_PROFILE=acct-b aws s3 ls   # routed to paws-b
```

> **Naming collision, not a relationship:** `AWS_PROFILE` set on the
> **daemon** container (see "With an AWS profile" in step 4) selects which
> profile the daemon's own `~/.aws/config` uses to reach real AWS. It is
> unrelated to the `AWS_PROFILE` described here, which is set on the **agent**
> container to pick which daemon to call. The two settings share a name but
> live on different containers and mean different things.

Run `aws --paws-version` inside the agent to confirm the wiring — it prints a
`profiles: <SUFFIX1> <SUFFIX2> ...` line listing every configured
`PAWS_URL_<SUFFIX>` pair (the line is omitted entirely if none are configured).

______________________________________________________________________

## 6. docker-compose example

```yaml
services:
  paws:
    image: ghcr.io/seefood/paws4claws:latest   # or paws4claws:local
    networks:
      - paws-net
    environment:
      AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID}
      AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY}
      AWS_DEFAULT_REGION: ${AWS_DEFAULT_REGION:-us-east-1}
      PAWS_TOKEN_AGENT: ${PAWS_TOKEN}           # same value injected into agent below
    restart: unless-stopped

  agent:
    image: my-agent-image
    networks:
      - paws-net
    environment:
      PAWS_TOKEN: ${PAWS_TOKEN}
    # wrapper/aws must be inside my-agent-image at /usr/local/bin/aws

networks:
  paws-net:
    driver: bridge
```

Put credentials and the token in a `.env` file (never commit it):

```sh
# .env
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
PAWS_TOKEN=$(openssl rand -hex 32)
```

Start everything:

```sh
docker compose up -d
```

______________________________________________________________________

## 7. Verify from inside the agent container

```sh
# from a shell inside the agent container:
aws sts get-caller-identity | jq .
```

Expected: the caller identity JSON from AWS (account ID, ARN, user ID).

If you see `paws: daemon unreachable`, check:

- Both containers are on `paws-net` (`docker network inspect paws-net`)
- The daemon is running (`docker ps`, `docker logs paws`)
- The daemon is named `paws` (or override `PAWS_URL` in the agent)

If you see `unauthorized` on stderr, the `PAWS_TOKEN` in the agent does not
match any `PAWS_TOKEN_*` in the daemon.

______________________________________________________________________

## 8. For AI agent integrations (nanoclaw and others)

This section summarises everything an agent skill needs to know.

### What the agent container must have

| Requirement          | Detail                                                               |
| -------------------- | -------------------------------------------------------------------- |
| `curl`               | Used by the wrapper to send HTTP requests to the daemon              |
| `jq`                 | Used by the wrapper to build the JSON payload and parse the response |
| `/usr/local/bin/aws` | The wrapper script from `wrapper/aws` in this repo, chmod +x         |
| `PAWS_TOKEN`         | Env var set at container startup — never baked into the image        |
| `paws-net`           | Container must be on this Docker network                             |

### What the agent can assume

- `aws` behaves exactly like the real AWS CLI: same flags, same exit codes,
  same stdout/stderr. Pipe freely — output filtering happens locally before it
  reaches the LLM context.
- `PAWS_URL` defaults to `http://paws:7142`. Set it explicitly if the daemon
  has a different name or port.
- The daemon never returns credentials. All errors from the proxy start with
  `paws:` on stderr.

### v0.4 limitations the agent must know

- `aws s3 cp s3://bucket/key /local/path` — **allowed** (local destination)
- `aws s3 cp /local/path s3://bucket/key` — **allowed** (local source)
- `aws s3 cp s3://bucket/path /local/path` --recursive — **blocked** (local destination)
- `aws s3 cp /local/path s3://bucket/path` --recursive — **blocked** (local source)
- `aws s3 sync ./local s3://bucket/prefix` — **blocked** (local source)
- `aws s3 cp s3://bucket/key -` — **allowed** (stream to stdout, pipe it)
- `aws s3 cp s3://src s3://dst` — **allowed** (server-side copy)

sync and copying entire directories is planned for future releases, as it's a big jump in complexity.

### Default allowed services

`s3`, `ec2`, `logs`, `ssm`, `sts`, `iam`, `lambda`, `cloudformation`, `ecr`,
`secretsmanager`. The operator can override with `PAWS_ALLOWED_SERVICES`.

### Error signals

| stderr prefix              | Meaning                                          |
| -------------------------- | ------------------------------------------------ |
| *(none, non-zero exit)*    | AWS error — read stderr for the AWS message      |
| `paws:`                    | Proxy error — config, network, or unsupported op |
| `paws: daemon unreachable` | Container is not on `paws-net` or daemon is down |

### Example skills in this repo

| File                                                                                     | Purpose                                                                                             |
| ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| [`examples/nanoclaw/README.md`](examples/nanoclaw/README.md)                             | Why there are two nanoclaw skills (agent vs operator)                                               |
| [`examples/nanoclaw/use-paws/SKILL.md`](examples/nanoclaw/use-paws/SKILL.md)             | In-agent skill — how to use the `aws` wrapper (usage, file I/O, errors)                             |
| [`examples/nanoclaw/add-paws4claws/SKILL.md`](examples/nanoclaw/add-paws4claws/SKILL.md) | Operator skill — wiring nanoclaw to a PAWS daemon (three wrapper install modes, `NO_PROXY`, tokens) |
