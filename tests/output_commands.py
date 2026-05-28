"""AWS CLI argv patterns for v0.4 download / output file passing (daemon → agent)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutputCommandCase:
    """One S3 cp/mv download with a local destination at arg_index."""

    id: str
    args: list[str]
    arg_index: int
    file_bytes: bytes
    note: str = ""


OUTPUT_COMMAND_CASES: tuple[OutputCommandCase, ...] = (
    OutputCommandCase(
        id="s3_cp_download_bare_path",
        args=["s3", "cp", "s3://bucket/key", "PLACEHOLDER"],
        arg_index=3,
        file_bytes=b"download-bytes-no-trailing-newline",
    ),
    OutputCommandCase(
        id="s3_mv_download_bare_path",
        args=["s3", "mv", "s3://bucket/key", "PLACEHOLDER"],
        arg_index=3,
        file_bytes=b"\x00\x01binary-download",
    ),
)
