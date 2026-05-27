# AWS CLI file input — coverage map

PAWS proxies the `aws` shell command only (not boto3). Agents often need to pass
**file content** into AWS CLI commands. This document catalogs every pattern we
have identified, what v2 covers today, and what **v3 file passing** must add.

## How AWS CLI accepts file content

| Mechanism                       | Example                              | v2 (stdin pipe)                | v3 (local path)               |
| ------------------------------- | ------------------------------------ | ------------------------------ | ----------------------------- |
| **Positional `-`**              | `aws s3 cp - s3://b/k`               | Yes — pipe into wrapper        | N/A (use pipe or `-` in args) |
| **`file:///dev/stdin`**         | `--value file:///dev/stdin`          | Yes — pipe into wrapper        | N/A                           |
| **`fileb:///dev/stdin`**        | `--user-data fileb:///dev/stdin`     | Yes — pipe into wrapper        | N/A                           |
| **Inline value in argv**        | `--cli-input-json '{"ImageId":"…"}'` | N/A (no pipe needed)           | N/A                           |
| **Local path in argv**          | `aws s3 cp ./app.zip s3://b/k`       | No — blocked or not wired      | **v3 target**                 |
| **`file://` local URI in argv** | `--zip-file fileb://./bundle.zip`    | No — path must exist on daemon | **v3 target**                 |

### v2 agent usage (pipe)

```sh
# Upload bytes without a local file path in argv
echo "$DATA" | aws s3 cp - s3://bucket/key
echo "$DATA" | aws ssm put-parameter --name /p --value file:///dev/stdin --type String --overwrite
```

The wrapper detects piped stdin (`[ ! -t 0 ]`), base64-encodes it into the
`"stdin"` field, and the daemon passes it to `subprocess.run(input=…)`.

### Known AWS CLI limitation (not a PAWS bug)

**`--cli-input-json file:///dev/stdin` does not work** in AWS CLI v2 — even when
process stdin is wired correctly, the CLI returns `Invalid JSON received`. Affects
at least:

- `aws ec2 run-instances --cli-input-json file:///dev/stdin`
- `aws ecs register-task-definition --cli-input-json file:///dev/stdin`

**Workarounds today:** inline JSON in the `--cli-input-json` argument (allowed by
PAWS sanitization), or wait for v3 to inline a local `.json` file from the agent.

______________________________________________________________________

## v2 — verified stdin patterns (tested)

Canonical list: [`tests/stdin_commands.py`](../tests/stdin_commands.py). Integration
tests assert each shape passes sanitization and reaches `subprocess.run(input=…)`.

### Default service allowlist

| Service            | Command             | File parameter      | URI / sentinel       |
| ------------------ | ------------------- | ------------------- | -------------------- |
| **s3**             | `cp`                | positional source   | `-`                  |
| **ec2**            | `run-instances`     | `--user-data`       | `fileb:///dev/stdin` |
| **lambda**         | `invoke`            | `--payload`         | `fileb:///dev/stdin` |
| **ssm**            | `put-parameter`     | `--value`           | `file:///dev/stdin`  |
| **secretsmanager** | `create-secret`     | `--secret-string`   | `file:///dev/stdin`  |
| **cloudformation** | `validate-template` | `--template-body`   | `file:///dev/stdin`  |
| **iam**            | `create-policy`     | `--policy-document` | `file:///dev/stdin`  |
| **ecr**            | `put-image`         | `--image-manifest`  | `file:///dev/stdin`  |

### Requires expanded allowlist (`PAWS_ALLOWED_SERVICES=all` or custom)

| Service   | Command                    | File parameter     | URI / sentinel      | Notes                                                      |
| --------- | -------------------------- | ------------------ | ------------------- | ---------------------------------------------------------- |
| **s3api** | `put-object`               | `--body`           | `file:///dev/stdin` | Prefer `aws s3 cp -` on default allowlist                  |
| **ecs**   | `register-task-definition` | `--cli-input-json` | `file:///dev/stdin` | **Broken in AWS CLI** — listed for sanitization tests only |

Same `file:///dev/stdin` / `fileb:///dev/stdin` pattern applies generically to
most AWS CLI parameters documented as accepting `file://` or `fileb://` URIs.

______________________________________________________________________

## v3 — local file input (next release target)

v3 will detect **local file paths** in argv (and `file://` / `fileb://` URIs
pointing at agent-local files), inline content in a `"files"` array, materialize
temp files on the daemon, substitute paths, and clean up after exec.

### Priority 1 — blocked today by `check_file_io`

These fail with **501** on the daemon today:

| Service | Subcommand | Blocked argv pattern                  | Typical agent intent    |
| ------- | ---------- | ------------------------------------- | ----------------------- |
| **s3**  | `cp`       | `aws s3 cp ./local s3://…`            | Upload a file           |
| **s3**  | `cp`       | `aws s3 cp s3://… ./local`            | Download a file         |
| **s3**  | `mv`       | local path as source or dest          | Move involving local FS |
| **s3**  | `sync`     | `aws s3 sync ./dir s3://…` or reverse | Directory sync          |

v2 workaround for upload: `cat ./local | aws s3 cp - s3://…`
v2 workaround for download: `aws s3 cp s3://… - > ./local`

### Priority 2 — same services, `file://` local URI parameters

| Service            | Command                | Parameter                       | Example                  |
| ------------------ | ---------------------- | ------------------------------- | ------------------------ |
| **ec2**            | `run-instances`        | `--user-data`                   | `fileb://./bootstrap.sh` |
| **ec2**            | `import-key-pair`      | `--public-key-material`         | `fileb://./key.pub`      |
| **lambda**         | `invoke`               | `--payload`                     | `fileb://./event.json`   |
| **lambda**         | `update-function-code` | `--zip-file`                    | `fileb://./function.zip` |
| **ssm**            | `put-parameter`        | `--value`                       | `file://./secret.txt`    |
| **secretsmanager** | `create-secret`        | `--secret-string`               | `file://./secret`        |
| **secretsmanager** | `create-secret`        | `--secret-binary`               | `fileb://./blob`         |
| **cloudformation** | `create-stack`         | `--template-body`               | `file://./template.json` |
| **cloudformation** | `deploy`               | `--template-file`               | `file://./template.yaml` |
| **cloudformation** | `package`              | `--template-file`               | `file://./template.yaml` |
| **iam**            | `create-policy`        | `--policy-document`             | `file://./policy.json`   |
| **iam**            | `put-role-policy`      | `--policy-document`             | `file://./policy.json`   |
| **iam**            | `create-role`          | `--assume-role-policy-document` | `file://./trust.json`    |
| **ecr**            | `put-image`            | `--image-manifest`              | `file://./manifest.json` |
| **s3api**          | `put-object`           | `--body`                        | `fileb://./object.bin`   |

### Priority 3 — `--cli-input-json` / `--cli-input-yaml` (whole-document files)

Whole-command JSON/YAML blobs are common for bulk APIs. v3 should inline the
file and pass either:

- a temp path on the daemon (`file:///tmp/paws-…`), or
- inline JSON in argv if under size limits

| Service            | Command                    | Parameter          |
| ------------------ | -------------------------- | ------------------ |
| **ec2**            | `run-instances`            | `--cli-input-json` |
| **ecs**            | `register-task-definition` | `--cli-input-json` |
| **ecs**            | `create-service`           | `--cli-input-json` |
| **lambda**         | `create-function`          | `--cli-input-json` |
| **cloudformation** | `create-stack`             | `--cli-input-json` |
| **iam**            | `create-user`              | `--cli-input-json` |
| **logs**           | `create-log-group`         | `--cli-input-json` |

*(Many other services support `--cli-input-json`; scope v3 to commands on the
service allowlist as needed.)*

### Priority 4 — other file parameters (allowlist services)

| Service    | Command                                       | Parameter           | Notes                                       |
| ---------- | --------------------------------------------- | ------------------- | ------------------------------------------- |
| **logs**   | `put-log-events`                              | `--log-events`      | Often inline JSON; `file://` variant exists |
| **ssm**    | `send-command`                                | `--parameters`      | May reference S3 URLs or inline JSON        |
| **lambda** | `publish-layer-version`                       | `--zip-file`        | Layer upload                                |
| **ecr**    | `initiate-layer-upload` / `upload-layer-part` | layer tarball paths | Multi-step; harder                          |

______________________________________________________________________

## v3 open questions

1. **Detection heuristic** — `[ -f "$arg" ]` vs requiring `file://` prefix vs
   explicit allowlist of `(service, subcommand, param-index)` tuples.
1. **False positives** — an arg like `my-backup` that happens to match an
   existing file in the agent cwd vs an S3 key or log group name.
1. **Size limits** — same 10 MB cap as stdin/output, or separate limit per file?
1. **Multi-file commands** — `aws s3 sync`, CloudFormation `package`, ECR layer
   uploads may need multiple entries in `"files"`.
1. **Binary integrity** — stdout already fixed (`jq -j`); v3 must preserve exact
   bytes for downloads to local materialized paths on the agent side (separate
   from daemon temp files).

______________________________________________________________________

## Related code

| File                                                    | Role                                         |
| ------------------------------------------------------- | -------------------------------------------- |
| [`tests/stdin_commands.py`](../tests/stdin_commands.py) | v2 stdin argv catalog + pytest cases         |
| [`daemon/paws.py`](../daemon/paws.py)                   | `check_file_io`, `decode_stdin`              |
| [`wrapper/aws`](../wrapper/aws)                         | Pipe detection, base64 stdin, `jq -j` stdout |
| [`DESIGN.md`](../DESIGN.md)                             | Roadmap and wire protocol                    |
