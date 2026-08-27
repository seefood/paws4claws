---
name: use-paws
description: Use AWS CLI via the PAWS proxy — credential-isolated aws calls without holding credentials in the agent container
---

The `aws` command in this container is a proxy wrapper. It forwards your calls to the
PAWS daemon over HTTP and returns stdout/stderr transparently. You never see credentials.

> **Setup** (Docker network, tokens, wrapper install) is documented in the operator skill:
> https://github.com/seefood/paws4claws/blob/main/examples/nanoclaw/add-paws4claws/SKILL.md
> This skill is **runtime usage only**.

## Basic usage

Use `aws` exactly as you would the real CLI. **Always pipe or filter output** before it
reaches your context — raw AWS responses are often large:

```sh
aws sts get-caller-identity | jq '.Account'
aws s3 ls s3://my-bucket/prefix/ | grep "\.gz" | head -20
aws logs describe-log-groups --query 'logGroups[*].logGroupName' --output text
aws ec2 describe-instances --filters Name=tag:Env,Values=prod \
  --query 'Reservations[*].Instances[*].InstanceId' --output text
```

## File I/O (v0.2 / v0.3 / v0.4)

| Goal                  | Command                                                                                 |
| --------------------- | --------------------------------------------------------------------------------------- |
| Upload a local file   | `aws s3 cp ./local.bin s3://bucket/key`                                                 |
| Download to local     | `aws s3 cp s3://bucket/key ./local.bin` (v0.4)                                          |
| Upload via pipe       | `echo "$DATA" \| aws s3 cp - s3://bucket/key`                                           |
| Upload via flag + URI | `aws ssm put-parameter --name /p --value file://./secret.txt --type String --overwrite` |
| Download to stdout    | `aws s3 cp s3://bucket/key -` (pipe or redirect locally)                                |
| Server-side S3 copy   | `aws s3 cp s3://src s3://dst`                                                           |

File upload only triggers for **S3 positional paths** or **`file://` / `fileb://`**
after known flags (`--user-data`, `--payload`, `--value`, `--secret-string`,
`--template-body`, `--policy-document`, `--image-manifest`, etc.). Random args like
`production` in `aws logs describe-log-groups production` are never treated as files.

## Not yet supported

| Blocked                         | Workaround                                       |
| ------------------------------- | ------------------------------------------------ |
| `aws s3 cp --recursive … ./dir` | per-object `aws s3 cp s3://… - > ./local` (v0.5) |
| `aws s3 sync ./dir s3://…`      | not available (v0.5 planned)                     |

## Error handling

| stderr starts with         | Meaning                                               |
| -------------------------- | ----------------------------------------------------- |
| (nothing, non-zero exit)   | AWS error — read stderr for details                   |
| `paws:`                    | Proxy error — config, network, or unsupported feature |
| `paws: daemon unreachable` | PAWS container is down or not on `paws-net`           |

## Multiple AWS accounts (AWS_PROFILE)

This container is wired to more than one AWS account. There is **no default
profile** — set `AWS_PROFILE` before every `aws` call (or export it once per
shell) or the wrapper fails immediately with a `paws:` error, no network call
made:

```sh
AWS_PROFILE=simpleai aws sts get-caller-identity
AWS_PROFILE=exacto aws s3 ls
```

Run `aws --paws-version` to see which profiles are wired (`profiles: SIMPLEAI EXACTO` line) and confirm the wrapper/daemon versions match.

## Environment variables

| Variable               | Required | Default | Description                                               |
| ---------------------- | -------- | ------- | --------------------------------------------------------- |
| `AWS_PROFILE`          | ✅       | —       | Selects which daemon to call — see above                  |
| `PAWS_URL_<PROFILE>`   | ✅       | —       | Per-profile daemon address, injected at container startup |
| `PAWS_TOKEN_<PROFILE>` | ✅       | —       | Per-profile bearer token, injected at container startup   |
