# AWS CLI file input — coverage map

PAWS proxies the `aws` shell command only (not boto3). Agents often need to pass
**file content** into AWS CLI commands. This document catalogs every pattern we
have identified, what v0.2 covers today, and what **v0.3 file passing** adds.

## How AWS CLI accepts file content

| Mechanism                       | Example                              | v0.2 (stdin pipe)             | v0.3 (local path)             |
| ------------------------------- | ------------------------------------ | ----------------------------- | ----------------------------- |
| **Positional `-`**              | `aws s3 cp - s3://b/k`               | Yes — pipe into wrapper       | N/A (use pipe or `-` in args) |
| **`file:///dev/stdin`**         | `--value file:///dev/stdin`          | Yes — pipe into wrapper       | N/A                           |
| **`fileb:///dev/stdin`**        | `--user-data fileb:///dev/stdin`     | Yes — pipe into wrapper       | N/A                           |
| **Inline value in argv**        | `--cli-input-json '{"ImageId":"…"}'` | N/A (no pipe needed)          | N/A                           |
| **Local path in argv**          | `aws s3 cp ./app.zip s3://b/k`       | No — use v0.3 `files` payload | **Yes (v0.3)**                |
| **`file://` local URI in argv** | `--zip-file fileb://./bundle.zip`    | No — use v0.3 `files` payload | **Yes (v0.3)**                |

### v0.2 agent usage (pipe)

```sh
# Upload bytes without a local file path in argv
echo "$DATA" | aws s3 cp - s3://bucket/key
echo "$DATA" | aws ssm put-parameter --name /p --value file:///dev/stdin --type String --overwrite
```

The wrapper detects piped stdin (`[ ! -t 0 ]`), base64-encodes it into the
`"stdin"` field, and the daemon passes it to `subprocess.run(input=…)`.

### v0.3 agent usage (local file)

The wrapper inlines files **only** at:

1. **S3 `cp` / `mv` / `sync`** — positional local paths (`aws s3 cp ./app.zip s3://…`)
1. **Known file parameters** — when the previous arg is one of the flags below and the
   value is `file://` or `fileb://` pointing at an existing file

| Service            | Flag                |
| ------------------ | ------------------- |
| **ec2**            | `--user-data`       |
| **lambda**         | `--payload`         |
| **ssm**            | `--value`           |
| **secretsmanager** | `--secret-string`   |
| **secretsmanager** | `--secret-binary`   |
| **cloudformation** | `--template-body`   |
| **iam**            | `--policy-document` |
| **ecr**            | `--image-manifest`  |

Other argv tokens (e.g. `aws logs describe-log-groups production`) are **never** resolved
to local files, even if a matching filename exists in the cwd.

```sh
aws s3 cp ./app.zip s3://bucket/key
aws ssm put-parameter --name /p --value file://./secret.txt --type String --overwrite
```

### Known AWS CLI limitation (not a PAWS bug)

**`--cli-input-json file:///dev/stdin` does not work** in AWS CLI v2 — even when
process stdin is wired correctly, the CLI returns `Invalid JSON received`. Affects
at least:

- `aws ec2 run-instances --cli-input-json file:///dev/stdin`
- `aws ecs register-task-definition --cli-input-json file:///dev/stdin`

**Workarounds:** inline JSON in the `--cli-input-json` argument (allowed by PAWS
sanitization), or v0.3 inline of a local `.json` file via the `files` payload.

______________________________________________________________________

## v0.2 — verified stdin patterns (tested)

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

______________________________________________________________________

## v0.3 — local file input (implemented)

v0.3 detects **local file paths** in argv (and `file://` / `fileb://` URIs
pointing at agent-local files), inlines content in a `"files"` array, materializes
temp files on the daemon (binary 1:1), substitutes paths, and cleans up after exec.

**Input-only** — uploads and parameter files. Downloads to local paths remain
`aws s3 cp s3://… - > ./local`.

**Not in v0.3.0:** `aws s3 sync`, directory recursion, response-side file return.

### Wire format

```json
{
  "args": ["s3", "cp", "./app.zip", "s3://bucket/key"],
  "files": [{"argIndex": 2, "content": "<base64>"}]
}
```

### Priority 1 — S3 local paths (formerly 501)

| Service | Subcommand | Example                      |
| ------- | ---------- | ---------------------------- |
| **s3**  | `cp`       | `aws s3 cp ./local s3://…`   |
| **s3**  | `mv`       | local path as source or dest |

### Priority 2 — `file://` / `fileb://` parameters

See [tests/file_commands.py](../tests/file_commands.py) and Priority 2 table in git
history for lambda, iam, ssm, cloudformation, secretsmanager, ecr, s3api cases.

### Deferred (roadmap)

| Feature                             | Target | Workaround                                 |
| ----------------------------------- | ------ | ------------------------------------------ |
| S3 download to `./local`            | v0.4   | `aws s3 cp s3://… - > ./local`             |
| `aws s3 sync`                       | v0.5   | not available                              |
| `--cli-input-json` via `/dev/stdin` | —      | inline JSON or v0.3 local file via `files` |
| Streaming / large files             | future | 10 MB inline cap today                     |

Multiple IAM profiles per token is **not planned** — run separate PAWS daemon containers.

______________________________________________________________________

## Related code

| File                                                    | Role                                              |
| ------------------------------------------------------- | ------------------------------------------------- |
| [`tests/stdin_commands.py`](../tests/stdin_commands.py) | v0.2 stdin argv catalog + pytest cases            |
| [`tests/file_commands.py`](../tests/file_commands.py)   | v0.3 file argv catalog + pytest cases             |
| [`daemon/paws.py`](../daemon/paws.py)                   | `decode_files`, `materialize_files`, sanitization |
| [`wrapper/aws`](../wrapper/aws)                         | File detection, base64 payload, `jq -j` stdout    |
