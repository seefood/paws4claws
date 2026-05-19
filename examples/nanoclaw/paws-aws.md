---
name: paws-aws
description: Use AWS CLI via the PAWS proxy — credential-isolated aws calls without holding credentials in the agent container
---

The `aws` command in this container is a proxy wrapper. It forwards your calls to the
PAWS daemon over HTTP and returns stdout/stderr transparently. You never see credentials.

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

## v1 limitations — local file I/O is not supported

These will fail with a `not_implemented` error (501):

- `aws s3 cp s3://bucket/key /local/path` — local destination ❌
- `aws s3 cp /local/path s3://bucket/key` — local source ❌
- `aws s3 sync ./local s3://bucket/prefix` — local source ❌

Allowed alternatives:

```sh
aws s3 cp s3://bucket/key -         # stream to stdout — pipe before it hits context
aws s3 cp s3://src s3://dst         # server-side copy ✅
aws s3 mv s3://src s3://dst         # server-side move ✅
```

## Error handling

| stderr starts with         | Meaning                                               |
| -------------------------- | ----------------------------------------------------- |
| (nothing, non-zero exit)   | AWS error — read stderr for details                   |
| `paws:`                    | Proxy error — config, network, or unsupported feature |
| `paws: daemon unreachable` | PAWS container is down or not on `paws-net`           |

## Environment variables

| Variable     | Required | Default            | Description                                     |
| ------------ | -------- | ------------------ | ----------------------------------------------- |
| `PAWS_TOKEN` | ✅       | —                  | Bearer token, injected at container startup     |
| `PAWS_URL`   | No       | `http://paws:7142` | Daemon address; override for non-default setups |
