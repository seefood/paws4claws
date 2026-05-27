"""Tests for v0.3 wrapper file allowlist (wrapper/file_allowlist.sh)."""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = REPO_ROOT / "wrapper" / "file_allowlist.sh"


def _collect_files(cwd: Path, *argv: str) -> list[dict]:
    script = f"""
set -e
. "{ALLOWLIST}"
collect_inline_files {" ".join(json.dumps(a) for a in argv)}
"""
    result = subprocess.run(
        ["sh", "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_s3_bare_path_is_collected(tmp_path: Path):
    upload = tmp_path / "app.bin"
    upload.write_bytes(b"payload-bytes")
    files = _collect_files(tmp_path, "s3", "cp", "./app.bin", "s3://bucket/key")
    assert len(files) == 1
    assert files[0]["argIndex"] == 2


def test_logs_group_name_is_not_collected(tmp_path: Path):
    (tmp_path / "production").write_bytes(b"accidental-file")
    files = _collect_files(tmp_path, "logs", "describe-log-groups", "production")
    assert files == []


def test_ssm_value_file_uri_is_collected(tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("value")
    files = _collect_files(
        tmp_path,
        "ssm",
        "put-parameter",
        "--name",
        "/p",
        "--value",
        f"file://{secret}",
        "--type",
        "String",
    )
    assert len(files) == 1
    assert files[0]["argIndex"] == 5


def test_bare_path_after_file_flag_is_not_collected(tmp_path: Path):
    doc = tmp_path / "policy.json"
    doc.write_text("{}")
    files = _collect_files(
        tmp_path,
        "iam",
        "create-policy",
        "--policy-name",
        "p",
        "--policy-document",
        "./policy.json",
    )
    assert files == []


def test_file_uri_without_allowlist_flag_is_not_collected(tmp_path: Path):
    doc = tmp_path / "x.json"
    doc.write_text("{}")
    files = _collect_files(tmp_path, "logs", "filter-log-events", f"file://{doc}")
    assert files == []
