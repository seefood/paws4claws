---
name: add-paws4claws
description: Install PAWS (paws4claws) as an AWS credential proxy for agent containers — replaces the awscli apt package with a lightweight wrapper that forwards aws calls to a credential daemon, keeping credentials out of containers entirely.
---

# Add PAWS AWS Credential Proxy

[paws4claws](https://github.com/seefood/paws4claws) runs AWS credentials in a dedicated daemon container. Agent containers get a drop-in `aws` wrapper that proxies calls over HTTP — no credentials, no `.aws` mount, no AWS SDK inside containers.

> **Agent usage** (how to run `aws`, file I/O patterns, errors) is in
> [`use-paws/SKILL.md`](../use-paws/SKILL.md). This skill is **operator setup only**.

## How it works

```
agent container ──aws cmd──► wrapper (/usr/local/bin/aws)
                                  │  HTTP POST /invoke  (PAWS_TOKEN auth)
                             paws daemon (holds ~/.aws creds)
                                  │
                             real aws-cli ──► AWS API
```

The `aws` command behaves identically to the real CLI — same flags, same exit codes, same stdout/stderr. Agents don't know or care that they're using a proxy.

## Prerequisites

- Docker with the `paws4claws` image built (see below)
- `openssl` for token generation
- The paws repo at `~/paws` (or wherever you cloned it)

## 1. Build the paws daemon image

```bash
cd ~/paws
docker build -t paws4claws:local daemon/
```

Verify:

```bash
docker run --rm --entrypoint aws paws4claws:local --version
# aws-cli/2.x.x ...
```

## 2. Create the Docker network

Both the daemon and agent containers must share `paws-net`:

```bash
docker network create paws-net
```

## 3. Generate a bearer token

One token covers all nanoclaw agent containers (or generate one per agent group for finer control):

```bash
openssl rand -hex 32
```

## 4. Configure both `.env` files

**`~/paws/.env`** — daemon config. The daemon reads credentials from `~/.aws` (mounted read-only) and accepts calls bearing this token:

```bash
AWS_DEFAULT_REGION=us-east-1
PAWS_TOKEN_NANOCLAW=<token-from-step-3>
```

Add `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` here instead of the mount if you prefer env-based credentials. For EC2 instance profiles, neither is needed.

**`~/nanoclaw/.env`** — nanoclaw config. This token is injected into agent containers at spawn time:

```bash
PAWS_TOKEN=<same-token-as-above>
```

## 5. Run the paws daemon

```bash
docker run -d \
  --name paws \
  --restart unless-stopped \
  --network paws-net \
  -v ~/.aws:/root/.aws:ro \
  --env-file ~/paws/.env \
  paws4claws:local
```

Check it started:

```bash
docker logs paws
# paws: listening on 0.0.0.0:7142
```

## 6. Nanoclaw code changes (already applied in this branch)

These changes are checked into `ester2` — no manual edits needed for a fresh checkout. Documented here for reference.

### `container/Dockerfile`

- Removed `awscli` from the apt-get install list
- Added `jq` (required by the wrapper to build JSON payloads)
- Removed `ln -s /usr/bin/aws /usr/local/bin/aws`
- Added `COPY --chmod=755 file_allowlist.sh /usr/local/lib/paws/file_allowlist.sh`
- Added `COPY --chmod=755 aws /usr/local/bin/aws` (wrapper from `container/aws`, synced from paws4claws `wrapper/`)

### `src/container-runner.ts`

- Removed the `~/.aws` host mount block
- Added: read `PAWS_TOKEN` from `.env`; if set, add `--network paws-net` and `-e PAWS_TOKEN=…` to the `docker run` args
- Added `paws` to `NO_PROXY` / `no_proxy` alongside `.amazonaws.com` — critical: the OneCLI gateway sets `HTTP_PROXY` which would otherwise intercept plain-HTTP calls to `http://paws:7142`

## 7. Rebuild and restart

After changing the Dockerfile or container-runner, rebuild the agent image and restart:

```bash
pnpm run build            # compile host TS
cd container && ./build.sh && cd ..
systemctl --user restart "$(. setup/lib/install-slug.sh && systemd_unit)"
```

## 8. Verify

```bash
# Quick smoke test — run a throwaway container on paws-net:
source <(grep '^PAWS_TOKEN=' ~/nanoclaw/.env | tail -1)
docker run --rm \
  --entrypoint bash \
  --network paws-net \
  -e "PAWS_TOKEN=$PAWS_TOKEN" \
  -e "NO_PROXY=paws,.amazonaws.com,169.254.169.254" \
  -e "no_proxy=paws,.amazonaws.com,169.254.169.254" \
  nanoclaw-agent-v2-58d885a2:latest \
  -c 'aws sts get-caller-identity'
# Expected: {"UserId": "...", "Account": "...", "Arn": "..."}
```

Or from inside a running agent container (after the agent spawns on a message):

```bash
docker exec <container-name> bash -c 'aws sts get-caller-identity'
```

## File I/O limitations

| Blocked / not yet              | Use instead                                      |
| ------------------------------ | ------------------------------------------------ |
| `aws s3 cp s3://… /local/path` | supported via v0.4 `outputFiles`                 |
| `aws s3 cp --recursive …`      | not supported (v0.5)                             |
| `aws s3 cp /local/path s3://…` | `aws s3 cp ./local s3://…` (v0.3) or pipe to `-` |
| `aws s3 sync ./local s3://…`   | not available (v0.5 planned)                     |
| `aws s3 cp s3://src s3://dst`  | ✅ server-side copy                              |

## Troubleshooting

### `paws: daemon unreachable`

1. Both containers on `paws-net`? `docker network inspect paws-net`
1. Daemon running? `docker ps | grep paws`, `docker logs paws`
1. **`HTTP_PROXY` intercepting?** — the OneCLI gateway sets `HTTP_PROXY` inside containers. If `NO_PROXY` doesn't include `paws`, every curl to `http://paws:7142` is routed through the OneCLI proxy which rejects it. Confirm `NO_PROXY` contains `paws`:
   ```bash
   docker exec <agent-container> bash -c 'echo $NO_PROXY'
   # should include: paws,...
   ```
   If missing, the `container-runner.ts` change wasn't compiled (`pnpm run build` was skipped).

### `unauthorized` on stderr

The `PAWS_TOKEN` in the agent container doesn't match any `PAWS_TOKEN_*` in the daemon. Re-check both `.env` files have the same hex value.

### `aws` command not found

The wrapper wasn't baked into the image. Confirm `container/aws` and `container/file_allowlist.sh` exist (or copy from paws4claws `wrapper/`), Dockerfile `COPY` lines are present, then rebuild with `./container/build.sh`.

### Daemon not persisting across reboots

The container has `--restart unless-stopped` which survives crashes and reboots as long as Docker starts on boot. Verify: `systemctl is-enabled docker`. If Docker isn't enabled, either enable it or create a systemd unit for the paws container.
