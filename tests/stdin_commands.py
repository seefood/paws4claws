"""AWS CLI argv patterns that read process stdin when PAWS passes input= to subprocess.

Two mechanisms:

1. Positional ``-`` — only ``aws s3 cp`` / ``aws s3 mv`` (high-level S3 commands).
2. ``file:///dev/stdin`` or ``fileb:///dev/stdin`` on a parameter — generic AWS CLI
   file loading; the child reads PAWS-decoded bytes from its stdin fd.

``--cli-input-json file:///dev/stdin`` does *not* work in AWS CLI v2 (ParamValidation
error even when stdin is wired). Use inline JSON in args for those commands, or v3
file passing for local files.

ECS uses the same broken ``--cli-input-json file:///dev/stdin`` pattern; it is listed
under ``requires_allowlist`` for sanitization tests only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StdinCommandCase:
    """One AWS CLI invocation shape that consumes subprocess stdin."""

    id: str
    service: str
    args: list[str]
    stdin_bytes: bytes
    note: str = ""


# Patterns that work with PAWS v2 + default service allowlist.
STDIN_COMMAND_CASES: tuple[StdinCommandCase, ...] = (
    StdinCommandCase(
        id="s3_cp_upload",
        service="s3",
        args=["s3", "cp", "-", "s3://bucket/key"],
        stdin_bytes=b"hello from stdin\n",
        note="Only high-level S3 command with positional dash source",
    ),
    StdinCommandCase(
        id="ec2_user_data",
        service="ec2",
        args=[
            "ec2",
            "run-instances",
            "--image-id",
            "ami-1234567890abcdef0",
            "--instance-type",
            "t2.micro",
            "--user-data",
            "fileb:///dev/stdin",
            "--dry-run",
        ],
        stdin_bytes=b"#!/bin/bash\necho bootstrap\n",
    ),
    StdinCommandCase(
        id="lambda_invoke_payload",
        service="lambda",
        args=[
            "lambda",
            "invoke",
            "--function-name",
            "my-function",
            "--payload",
            "fileb:///dev/stdin",
            "-",
        ],
        stdin_bytes=b'{"key":"value"}',
        note="Output path '-' streams response to stdout (no upload stdin)",
    ),
    StdinCommandCase(
        id="ssm_put_parameter",
        service="ssm",
        args=[
            "ssm",
            "put-parameter",
            "--name",
            "/app/config",
            "--value",
            "file:///dev/stdin",
            "--type",
            "String",
            "--overwrite",
        ],
        stdin_bytes=b"config-value-from-stdin",
    ),
    StdinCommandCase(
        id="secretsmanager_create_secret",
        service="secretsmanager",
        args=[
            "secretsmanager",
            "create-secret",
            "--name",
            "my-secret",
            "--secret-string",
            "file:///dev/stdin",
        ],
        stdin_bytes=b"super-secret-value",
    ),
    StdinCommandCase(
        id="cloudformation_validate_template",
        service="cloudformation",
        args=[
            "cloudformation",
            "validate-template",
            "--template-body",
            "file:///dev/stdin",
        ],
        stdin_bytes=b'{"AWSTemplateFormatVersion":"2010-09-09","Resources":{}}',
    ),
    StdinCommandCase(
        id="iam_create_policy",
        service="iam",
        args=[
            "iam",
            "create-policy",
            "--policy-name",
            "MyPolicy",
            "--policy-document",
            "file:///dev/stdin",
        ],
        stdin_bytes=b'{"Version":"2012-10-17","Statement":[]}',
    ),
    StdinCommandCase(
        id="ecr_put_image",
        service="ecr",
        args=[
            "ecr",
            "put-image",
            "--repository-name",
            "my-repo",
            "--image-manifest",
            "file:///dev/stdin",
        ],
        stdin_bytes=b'{"schemaVersion":2,"mediaType":"application/vnd.docker.distribution.manifest.v2+json","config":{},"layers":[]}',
    ),
)

# Sanitize-only cases: valid argv shapes but service not in DEFAULT_ALLOWED_SERVICES.
STDIN_COMMAND_REQUIRES_ALLOWLIST: tuple[StdinCommandCase, ...] = (
    StdinCommandCase(
        id="ecs_register_task_definition",
        service="ecs",
        args=["ecs", "register-task-definition", "--cli-input-json", "file:///dev/stdin"],
        stdin_bytes=b'{"family":"web","containerDefinitions":[]}',
        note="ecs not in default allowlist; cli-input-json+file:///dev/stdin broken in AWS CLI",
    ),
    StdinCommandCase(
        id="s3api_put_object",
        service="s3api",
        args=[
            "s3api",
            "put-object",
            "--bucket",
            "my-bucket",
            "--key",
            "my-key",
            "--body",
            "file:///dev/stdin",
        ],
        stdin_bytes=b"binary-or-text-body",
        note="s3api not in default allowlist; prefer aws s3 cp - for uploads",
    ),
)
