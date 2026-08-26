---
name: add-paws4claws
description: Install PAWS (paws4claws) as an AWS credential proxy for agent containers — replaces the awscli apt package with a lightweight wrapper that forwards aws calls to a credential daemon, keeping credentials out of containers entirely.
---

# Add PAWS AWS Credential Proxy

[paws4claws](https://github.com/seefood/paws4claws) runs AWS credentials in a dedicated daemon container. Agent containers get a drop-in `aws` wrapper that proxies calls over HTTP — no credentials, no `.aws` mount, no AWS SDK inside containers.

> **Agent usage** — optional. The wrapper is installed as `aws` on `PATH` so agents
> need no PAWS-specific instructions. For in-context guidance (file I/O patterns,
> piping large output, `paws:` errors), install the agent skill from GitHub — see
> [§10](#10-agent-skill-optional). This skill is **operator setup only**.

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

- Docker
- `openssl` for token generation
- `wget` or `curl` on the host (to fetch wrapper files)
- Agent containers need **`curl`** and **`jq`** (no `awscli`, no git clone)

## Source URLs

Pin a release tag once. Use the same tag for the daemon image and wrapper files so versions stay aligned.

```bash
export PAWS_TAG=v0.5.0
export PAWS_RAW="https://raw.githubusercontent.com/seefood/paws4claws/${PAWS_TAG}"
export PAWS_IMAGE="ghcr.io/seefood/paws4claws:${PAWS_TAG#v}"
```

| Artifact                | Location                                                                                           |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| Daemon image            | `${PAWS_IMAGE}` (also `:latest` on GHCR after a release)                                           |
| Wrapper `aws`           | `${PAWS_RAW}/wrapper/aws`                                                                          |
| Wrapper allowlist       | `${PAWS_RAW}/wrapper/file_allowlist.sh`                                                            |
| Wrapper profile resolve | `${PAWS_RAW}/wrapper/profile_resolve.sh`                                                           |
| Agent skill (optional)  | `${PAWS_RAW}/examples/nanoclaw/use-paws/SKILL.md`                                                  |
| Operator skill (this)   | `https://github.com/seefood/paws4claws/blob/${PAWS_TAG}/examples/nanoclaw/add-paws4claws/SKILL.md` |

No git clone required. If you already have the repo, you may set `PAWS_REPO=~/paws` and use `cp` instead of `wget` — same paths under `wrapper/`.

## 1. Pull the paws daemon image

```bash
docker pull "${PAWS_IMAGE}"
```

Verify:

```bash
docker run --rm --entrypoint aws "${PAWS_IMAGE}" --version
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

## 4. Configure both env files

Create a small config directory on the host (no repo clone — only env files):

```bash
mkdir -p ~/paws
```

**`~/paws/.env`** — daemon config. The daemon reads credentials from `~/.aws` (mounted read-only) and accepts calls bearing this token:

```bash
AWS_DEFAULT_REGION=us-east-1
PAWS_TOKEN_NANOCLAW=<token-from-step-3>
```

Add `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` here instead of the mount if you prefer env-based credentials. For EC2 instance profiles, neither is needed.

**`~/nanoclaw/.env.paws`** — nanoclaw config, a **separate file from `.env`**. Nanoclaw auto-detects this file at the project root: if it exists, it's mounted read-only into every agent container (opaque — nanoclaw never parses it) and the container is joined onto `paws-net`. No `.env` edit, no source change, no manual mount needed.

Single daemon, no profile routing:

```bash
PAWS_TOKEN=<same-token-as-above>
```

Multiple daemons (e.g. separate AWS accounts) — one `PAWS_URL_<PROFILE>`/`PAWS_TOKEN_<PROFILE>` pair per daemon, where `<PROFILE>` is the uppercased `AWS_PROFILE` value agents will pass (hyphens become underscores, e.g. `acct-a` → `ACCT_A`):

```bash
PAWS_URL_ACCT_A=http://paws-acct-a:7142
PAWS_TOKEN_ACCT_A=<token-for-acct-a>
PAWS_URL_ACCT_B=http://paws-acct-b:7142
PAWS_TOKEN_ACCT_B=<token-for-acct-b>
```

Agents then set `AWS_PROFILE=acct-a` (or `acct-b`) before calling `aws` to route to the matching daemon.

## 5. Run the paws daemon

```bash
docker run -d \
  --name paws \
  --restart unless-stopped \
  --network paws-net \
  -v ~/.aws:/root/.aws:ro \
  --env-file ~/paws/.env \
  "${PAWS_IMAGE}"
```

Check it started:

```bash
docker logs paws
# paws: listening on 0.0.0.0:7142
```

## 6. Choose a wrapper install mode

The PAWS wrapper is **three files** (`wrapper/aws`, `wrapper/file_allowlist.sh`, `wrapper/profile_resolve.sh`), always colocated. Fetch them from `${PAWS_RAW}` (see [Source URLs](#source-urls)). Pick **one** mode below.

| Mode                | Where files live                             | Rebuild image?      | Upgrade wrapper                            |
| ------------------- | -------------------------------------------- | ------------------- | ------------------------------------------ |
| **C (recommended)** | Host `~/bin`, mounted R/W into the container | No                  | Re-`wget` on host; no container restart    |
| **B**               | Host dir, bind-mounted read-only at spawn    | No (respawn agents) | Re-`wget` on host; new containers pick up  |
| **A**               | Baked into the agent image (`COPY`)          | Yes                 | Re-`wget` into `container/`, rebuild image |

The wrapper finds `file_allowlist.sh` and `profile_resolve.sh` next to the `aws` script (`dirname "$0"`), or at `/usr/local/lib/paws/` (mode A layout).

### Mode C — Host `~/bin` (recommended)

**Best for:** simplest install and fastest upgrades. Nanoclaw typically mounts the agent homedir from the host; `~/bin` inside the container is a host directory you can edit without rebuilding or restarting.

1. Ensure the agent image has **`curl`** and **`jq`** (no `awscli`, no wrapper `COPY`).
1. Download all three wrapper files into the agent's **host** `bin` directory (same folder — the path that appears as `~/bin` inside the container):

```bash
AGENT_BIN=~/nanoclaw/data/agents/main/bin   # adjust to your homedir layout
mkdir -p "$AGENT_BIN"
wget -q "${PAWS_RAW}/wrapper/aws" -O "$AGENT_BIN/aws"
wget -q "${PAWS_RAW}/wrapper/file_allowlist.sh" -O "$AGENT_BIN/file_allowlist.sh"
wget -q "${PAWS_RAW}/wrapper/profile_resolve.sh" -O "$AGENT_BIN/profile_resolve.sh"
chmod +x "$AGENT_BIN/aws"
```

1. Confirm `~/bin` is on `PATH` inside the container (nanoclaw default for many setups).

No `container-runner` volume mounts or Dockerfile `COPY` lines are required for the wrapper.

### Mode B — Runtime read-only bind mount

**Best for:** one canonical wrapper directory on the host, shared across agents, without baking into the image.

1. Install files on the host (all three in the same directory):

```bash
mkdir -p ~/paws/wrapper
wget -q "${PAWS_RAW}/wrapper/aws" -O ~/paws/wrapper/aws
wget -q "${PAWS_RAW}/wrapper/file_allowlist.sh" -O ~/paws/wrapper/file_allowlist.sh
wget -q "${PAWS_RAW}/wrapper/profile_resolve.sh" -O ~/paws/wrapper/profile_resolve.sh
chmod +x ~/paws/wrapper/aws
```

1. Mount the directory into the agent group's containers via `ncl` — no source edit:

```bash
ncl groups config add-mount --id <group-id> \
  --host ~/paws/wrapper --container /opt/paws --ro
ncl groups restart --id <group-id>
```

Put `/opt/paws` on `PATH` inside the container (whatever env-injection your container config supports) so `aws` resolves there.

Upgrading later is a re-`wget` into `~/paws/wrapper/` followed by `ncl groups restart --id <group-id>` — no rebuild, no source edit.

### Mode A — Bake into the agent image (Dockerfile)

**Best for:** operators who want the wrapper fixed inside the image. **Slowest** install and upgrade (rebuild + restart every time).

1. Download into nanoclaw `container/`:

```bash
wget -q "${PAWS_RAW}/wrapper/aws" -O ~/nanoclaw/container/aws
wget -q "${PAWS_RAW}/wrapper/file_allowlist.sh" -O ~/nanoclaw/container/file_allowlist.sh
wget -q "${PAWS_RAW}/wrapper/profile_resolve.sh" -O ~/nanoclaw/container/profile_resolve.sh
chmod +x ~/nanoclaw/container/aws
```

1. In **`container/Dockerfile`**:

```dockerfile
RUN apt-get install -y --no-install-recommends jq curl   # no awscli
COPY --chmod=755 file_allowlist.sh /usr/local/lib/paws/file_allowlist.sh
COPY --chmod=755 profile_resolve.sh /usr/local/lib/paws/profile_resolve.sh
COPY --chmod=755 aws /usr/local/bin/aws
```

1. Rebuild the agent image (see §8).

## 7. Nanoclaw changes (all modes)

None. Nanoclaw auto-detects `~/nanoclaw/.env.paws` (§4) at the project root: if present, it mounts the file read-only into every agent container at `/run/paws/paws.env` and joins the container onto `paws-net` — both handled by nanoclaw's session composition, not a manual `container-runner.ts` edit. Adding, renaming, or removing a PAWS profile is purely an edit to `.env.paws`.

`~/paws/wrapper/aws` sources the mounted file into its own short-lived shell before resolving `PAWS_URL`/`PAWS_TOKEN`, so no PAWS credential ever rides as a container-wide env var. No `NO_PROXY`/`no_proxy` configuration is needed either — nanoclaw's OneCLI gateway only ever sets `HTTPS_PROXY`, which does not intercept PAWS's plain-HTTP traffic.

### Mode-specific extras

| Mode | Dockerfile wrapper `COPY` | Wrapper delivery                          |
| ---- | ------------------------- | ----------------------------------------- |
| C    | None                      | Host `~/bin` (already on `PATH`)          |
| B    | None                      | `ncl groups config add-mount` (§6 mode B) |
| A    | Yes (§6 mode A)           | Baked into the image                      |

## 8. Rebuild and restart

| Mode  | When you need a rebuild / restart                                                        |
| ----- | ---------------------------------------------------------------------------------------- |
| **C** | Only when changing agent image deps (`jq`, `curl`) — **not** for wrapper-only upgrades   |
| **B** | After adding/changing the mount: `ncl groups restart --id <group-id>` — no image rebuild |
| **A** | After any Dockerfile or wrapper change: `cd container && ./build.sh` + restart nanoclaw  |

```bash
cd container && ./build.sh && cd ..   # mode A only (image rebuild)
systemctl --user restart "$(. setup/lib/install-slug.sh && systemd_unit)"
```

## 9. Verify

Use the same **PATH and volume mounts** as production. Replace `nanoclaw-agent-v2-58d885a2:latest` with your image tag.

**Version check** (no `PAWS_TOKEN`, no AWS call):

```bash
docker run --rm \
  --network paws-net \
  nanoclaw-agent-v2-58d885a2:latest \
  aws --paws-version
# Expected (versions aligned after upgrade):
#   wrapper: 0.5.0
#   daemon:  0.5.0
# Exit 1 + stderr if wrapper and daemon differ (version drift).
```

For **mode B**, add the mount you set up with `ncl groups config add-mount` (and its `PATH` injection) to the command above. For **mode C**, run verify from a **running** agent container (host `~/bin` is mounted there):

```bash
docker exec <container-name> aws --paws-version
```

**Smoke test** (`PAWS_TOKEN` required):

```bash
source <(grep '^PAWS_TOKEN=' ~/nanoclaw/.env.paws | tail -1)
docker run --rm \
  --network paws-net \
  -e "PAWS_TOKEN=$PAWS_TOKEN" \
  nanoclaw-agent-v2-58d885a2:latest \
  aws sts get-caller-identity
```

Or from a running agent: `docker exec <container-name> aws sts get-caller-identity`

## 10. Agent skill (optional)

PAWS is designed to be **transparent**: the proxy is the `aws` command on `PATH`, with the
same flags, exit codes, and stdout/stderr as the real CLI. Agents that already know `aws`
do not need any extra skill or documentation to use PAWS.

**Optional:** install the in-agent skill into your claw's agent skills directory (not next to
this operator skill — they live in different places):

```bash
AGENT_SKILLS=~/nanoclaw/data/agents/main/skills   # adjust to your layout
mkdir -p "$AGENT_SKILLS/use-paws"
wget -q "${PAWS_RAW}/examples/nanoclaw/use-paws/SKILL.md" -O "$AGENT_SKILLS/use-paws/SKILL.md"
```

Browser link (same file): `${PAWS_RAW}/examples/nanoclaw/use-paws/SKILL.md`

That skill documents runtime patterns that are easy to get wrong without reading the repo:

- piping or filtering large AWS output before it hits context
- v0.3 upload paths (`./file` vs `file://`) and v0.4 local download destinations
- interpreting `paws:` proxy errors vs ordinary AWS failures

Skip this step if you prefer agents to discover `aws` on their own; nothing in nanoclaw or
the wrapper requires the skill to be present.

## 11. Upgrading PAWS

1. Set `PAWS_TAG` to the new release (e.g. `v0.5.1`) and refresh [Source URLs](#source-urls).
1. Pull the new daemon image and restart the `paws` container:

```bash
docker pull "${PAWS_IMAGE}"
docker stop paws && docker rm paws
# re-run §5 docker run with the new image
```

1. Re-fetch the wrapper per mode:

| Mode  | Wrapper upgrade steps                                                                                    |
| ----- | -------------------------------------------------------------------------------------------------------- |
| **C** | Re-run the `wget` lines from §6 mode C into host `~/bin` — **no container restart**                      |
| **B** | Re-run the `wget` lines from §6 mode B into `~/paws/wrapper/`, then `ncl groups restart --id <group-id>` |
| **A** | Re-run the `wget` lines from §6 mode A into `container/`, rebuild agent image, restart nanoclaw          |

1. Optional: re-`wget` the agent skill (`§10`).
1. Run `aws --paws-version` to confirm wrapper and daemon match.

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
1. `.env.paws` present at the nanoclaw project root and non-empty? Nanoclaw only mounts and joins `paws-net` when the file exists (§7) — a missing or empty file means the container never got credentials or network access.

### `unauthorized` on stderr

The `PAWS_TOKEN` (or `PAWS_TOKEN_<PROFILE>`) in `.env.paws` doesn't match any token the daemon knows. Re-check `.env.paws` and the daemon's `~/paws/.env` have the same hex value for that profile.

### `aws` command not found

| Mode  | Check                                                                                                                                                                 |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **C** | All three files on the **host** `bin` path that mounts as `~/bin`; `which aws` inside the container shows `~/bin/aws`                                                 |
| **B** | Mount present on `docker inspect <container>` (or `ncl groups config get --id <group-id>`); host files exist under `~/paws/wrapper/`; `PATH` includes the mount point |
| **A** | `container/aws`, `container/file_allowlist.sh`, `container/profile_resolve.sh` present; Dockerfile `COPY` lines present; image rebuilt                                |

### `paws: file_allowlist.sh not found` / `paws: profile_resolve.sh not found`

The wrapper could not find one of its companion scripts. **Mode C / B (colocated):** `file_allowlist.sh` and `profile_resolve.sh` must sit in the **same directory** as the `aws` script. **Mode A:** confirm `/usr/local/lib/paws/file_allowlist.sh` and `/usr/local/lib/paws/profile_resolve.sh` exist in the image.

### Daemon not persisting across reboots

The container has `--restart unless-stopped` which survives crashes and reboots as long as Docker starts on boot. Verify: `systemctl is-enabled docker`. If Docker isn't enabled, either enable it or create a systemd unit for the paws container.
