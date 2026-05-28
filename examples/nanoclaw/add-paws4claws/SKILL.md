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
agent container ──aws cmd──► wrapper (~/bin/aws, or mounted path — see §6)
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

## 6. Choose a wrapper install mode

The PAWS wrapper is **two files** from the paws4claws repo (`wrapper/aws` and `wrapper/file_allowlist.sh`). Pick **one** mode below.

| Mode                | Where files live                             | Rebuild image?      | Upgrade wrapper                              |
| ------------------- | -------------------------------------------- | ------------------- | -------------------------------------------- |
| **C (recommended)** | Host `~/bin`, mounted R/W into the container | No                  | Copy two files on host; no container restart |
| **B**               | Host dir, bind-mounted read-only at spawn    | No (respawn agents) | Replace host files; new containers pick up   |
| **A**               | Baked into the agent image (`COPY`)          | Yes                 | Sync into `container/`, rebuild image        |

The wrapper finds `file_allowlist.sh` next to the `aws` script (`dirname "$0"`), or at `/usr/local/lib/paws/file_allowlist.sh` (mode A layout).

Set `PAWS_REPO=~/paws` (or your clone path) for the commands below.

### Mode C — Host `~/bin` (recommended)

**Best for:** simplest install and fastest upgrades. Nanoclaw typically mounts the agent homedir from the host; `~/bin` inside the container is a host directory you can edit without rebuilding or restarting.

1. Ensure the agent image has **`curl`** and **`jq`** (no `awscli`, no wrapper `COPY`).
1. Copy both wrapper files into the agent's **host** `bin` directory (same folder — the path that appears as `~/bin` inside the container):

```bash
AGENT_BIN=~/nanoclaw/data/agents/main/bin   # adjust to your homedir layout
mkdir -p "$AGENT_BIN"
cp "$PAWS_REPO/wrapper/aws" "$PAWS_REPO/wrapper/file_allowlist.sh" "$AGENT_BIN/"
chmod +x "$AGENT_BIN/aws"
```

1. Confirm `~/bin` is on `PATH` inside the container (nanoclaw default for many setups).

No `container-runner` volume mounts or Dockerfile `COPY` lines are required for the wrapper.

### Mode B — Runtime read-only bind mount

**Best for:** one canonical wrapper directory on the host, shared across agents, without baking into the image.

1. Install files on the host (both in the same directory):

```bash
mkdir -p ~/paws/wrapper
cp "$PAWS_REPO/wrapper/aws" "$PAWS_REPO/wrapper/file_allowlist.sh" ~/paws/wrapper/
chmod +x ~/paws/wrapper/aws
```

1. In **`src/container-runner.ts`**, when `PAWS_TOKEN` is set, add bind mounts to each `docker run` (paths must match inside the container):

```typescript
// Option 1 — standard layout (matches mode A paths):
'-v', `${process.env.HOME}/paws/wrapper/aws:/usr/local/bin/aws:ro`,
'-v', `${process.env.HOME}/paws/wrapper/file_allowlist.sh:/usr/local/lib/paws/file_allowlist.sh:ro`,

// Option 2 — single directory + PATH (both files colocated):
'-v', `${process.env.HOME}/paws/wrapper:/opt/paws:ro`,
// and inject: -e PATH=/opt/paws:${existingPath}
```

1. Recompile host TS (`pnpm run build`). **Respawn** agent containers after upgrading wrapper files on the host (no full image rebuild).

### Mode A — Bake into the agent image (Dockerfile)

**Best for:** operators who want the wrapper fixed inside the image. **Slowest** install and upgrade (rebuild + restart every time).

1. Sync from paws4claws into nanoclaw `container/`:

```bash
cp "$PAWS_REPO/wrapper/aws" "$PAWS_REPO/wrapper/file_allowlist.sh" ~/nanoclaw/container/
```

1. In **`container/Dockerfile`** (ester2 branch may already have this):

```dockerfile
RUN apt-get install -y --no-install-recommends jq curl   # no awscli
COPY --chmod=755 file_allowlist.sh /usr/local/lib/paws/file_allowlist.sh
COPY --chmod=755 aws /usr/local/bin/aws
```

1. Rebuild the agent image (see §8).

## 7. Nanoclaw changes (all modes)

These apply regardless of wrapper install mode. On the `ester2` branch many are already applied.

### `src/container-runner.ts`

- Removed the `~/.aws` host mount block
- When `PAWS_TOKEN` is set in `~/nanoclaw/.env`: add `--network paws-net` and `-e PAWS_TOKEN=…` to agent `docker run`
- Add **`paws`** to `NO_PROXY` / `no_proxy` alongside `.amazonaws.com` — the OneCLI gateway sets `HTTP_PROXY`, which otherwise intercepts `http://paws:7142`

### Mode-specific extras

| Mode | Dockerfile wrapper `COPY` | container-runner bind mounts |
| ---- | ------------------------- | ---------------------------- |
| C    | None                      | None                         |
| B    | None                      | Yes (§6 mode B)              |
| A    | Yes (§6 mode A)           | Optional                     |

## 8. Rebuild and restart

| Mode  | When you need a rebuild / restart                                                                               |
| ----- | --------------------------------------------------------------------------------------------------------------- |
| **C** | Only when changing agent image deps (`jq`, `curl`) or `container-runner.ts` — **not** for wrapper-only upgrades |
| **B** | After `container-runner.ts` mount changes: `pnpm run build` + respawn agents                                    |
| **A** | After any Dockerfile or wrapper change: `cd container && ./build.sh` + restart nanoclaw                         |

```bash
pnpm run build            # compile host TS (modes B, A, or runner changes)
cd container && ./build.sh && cd ..   # mode A only (image rebuild)
systemctl --user restart "$(. setup/lib/install-slug.sh && systemd_unit)"
```

## 9. Verify

Use the same **PATH and volume mounts** as production. Replace `nanoclaw-agent-v2-58d885a2:latest` with your image tag.

**Version check** (no `PAWS_TOKEN`, no AWS call):

```bash
docker run --rm \
  --network paws-net \
  -e "NO_PROXY=paws,.amazonaws.com,169.254.169.254" \
  -e "no_proxy=paws,.amazonaws.com,169.254.169.254" \
  nanoclaw-agent-v2-58d885a2:latest \
  aws --paws-version
# Expected (versions aligned after upgrade):
#   wrapper: 0.4.0
#   daemon:  0.4.0
# Exit 1 + stderr if wrapper and daemon differ (version drift).
```

For **mode B**, add the same `-v` / `-e PATH=…` flags you use in `container-runner.ts`. For **mode C**, run verify from a **running** agent container (host `~/bin` is mounted there):

```bash
docker exec <container-name> aws --paws-version
```

**Smoke test** (`PAWS_TOKEN` required):

```bash
source <(grep '^PAWS_TOKEN=' ~/nanoclaw/.env | tail -1)
docker run --rm \
  --network paws-net \
  -e "PAWS_TOKEN=$PAWS_TOKEN" \
  -e "NO_PROXY=paws,.amazonaws.com,169.254.169.254" \
  -e "no_proxy=paws,.amazonaws.com,169.254.169.254" \
  nanoclaw-agent-v2-58d885a2:latest \
  aws sts get-caller-identity
```

Or from a running agent: `docker exec <container-name> aws sts get-caller-identity`

## 10. Upgrading PAWS

After pulling a new paws4claws release, bump **both** `PAWS_WRAPPER_VERSION` in `wrapper/aws` and `VERSION` in `daemon/paws.py`, rebuild/restart the **daemon** image, then upgrade the wrapper per mode:

| Mode  | Wrapper upgrade steps                                                                         |
| ----- | --------------------------------------------------------------------------------------------- |
| **C** | `cp` `wrapper/aws` and `wrapper/file_allowlist.sh` to host `~/bin` — **no container restart** |
| **B** | `cp` both files to `~/paws/wrapper/` — respawn agent containers                               |
| **A** | `cp` into `container/`, rebuild agent image, restart nanoclaw                                 |

Run `aws --paws-version` after upgrading to confirm wrapper and daemon match.

## File I/O limitations

| Blocked / not yet              | Use instead                                      |
| ------------------------------ | ------------------------------------------------ |
| `aws s3 cp s3://… /local/path` | supported via v0.4 `outputFiles`                 |
| `aws s3 cp --recursive …`      | not supported (v0.5)                             |
| `aws s3 cp /local/path s3://…` | `aws s3 cp ./local s3://…` (v0.3) or pipe to `-` |
| `aws s3 sync ./local s3://…`   | not available (v0.5 planned)                     |
| `aws s3 cp s3://src s3://dst`  | server-side copy                                 |

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

| Mode  | Check                                                                                                            |
| ----- | ---------------------------------------------------------------------------------------------------------------- |
| **C** | Both files on the **host** `bin` path that mounts as `~/bin`; `which aws` inside the container shows `~/bin/aws` |
| **B** | Bind mounts present on `docker inspect <container>`; host files exist under `~/paws/wrapper/`                    |
| **A** | `container/aws` synced from paws4claws; Dockerfile `COPY` lines present; image rebuilt                           |

### `paws: file_allowlist.sh not found`

The wrapper could not find its allowlist. **Mode C / B (colocated):** `file_allowlist.sh` must sit in the **same directory** as the `aws` script. **Mode A:** confirm `/usr/local/lib/paws/file_allowlist.sh` exists in the image.

### Daemon not persisting across reboots

The container has `--restart unless-stopped` which survives crashes and reboots as long as Docker starts on boot. Verify: `systemctl is-enabled docker`. If Docker isn't enabled, either enable it or create a systemd unit for the paws container.
