"""AWS CLI argv patterns for v0.3 local file passing (input-only, agent → daemon)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileCommandCase:
    """One AWS CLI invocation with an inline file at argIndex."""

    id: str
    service: str
    args: list[str]
    arg_index: int
    file_bytes: bytes
    note: str = ""


FILE_COMMAND_CASES: tuple[FileCommandCase, ...] = (
    FileCommandCase(
        id="s3_cp_upload_bare_path",
        service="s3",
        args=["s3", "cp", "PLACEHOLDER", "s3://bucket/key"],
        arg_index=2,
        file_bytes=b"binary-upload-no-trailing-newline",
    ),
    FileCommandCase(
        id="s3_cp_upload_file_uri",
        service="s3",
        args=["s3", "cp", "file://PLACEHOLDER", "s3://bucket/key"],
        arg_index=2,
        file_bytes=b"\x00\x01binary",
        note="Daemon substitutes file:///…/paws-* temp path",
    ),
    FileCommandCase(
        id="lambda_invoke_payload_uri",
        service="lambda",
        args=[
            "lambda",
            "invoke",
            "--function-name",
            "my-function",
            "--payload",
            "fileb://PLACEHOLDER",
            "-",
        ],
        arg_index=5,
        file_bytes=b'{"key":"value"}',
    ),
    FileCommandCase(
        id="iam_policy_document",
        service="iam",
        args=[
            "iam",
            "create-policy",
            "--policy-name",
            "MyPolicy",
            "--policy-document",
            "file://PLACEHOLDER",
        ],
        arg_index=5,
        file_bytes=b'{"Version":"2012-10-17","Statement":[]}',
    ),
    FileCommandCase(
        id="ssm_put_parameter_value",
        service="ssm",
        args=[
            "ssm",
            "put-parameter",
            "--name",
            "/app/config",
            "--value",
            "file://PLACEHOLDER",
            "--type",
            "String",
            "--overwrite",
        ],
        arg_index=5,
        file_bytes=b"config-from-file",
    ),
)
