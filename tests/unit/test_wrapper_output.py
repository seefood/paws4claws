"""Wrapper output must preserve exact stdout/stderr bytes (e.g. aws s3 cp … -)."""

import json
import subprocess


def _jq_stdout(flag: str, payload: dict) -> bytes:
    proc = subprocess.run(
        ["jq", flag, ".stdout"],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
    )
    return proc.stdout


def test_jq_raw_output_appends_newline():
    """Documents why the wrapper uses jq -j, not jq -r, for stdout."""
    payload = {"exitCode": 0, "stdout": "abc", "stderr": ""}
    assert _jq_stdout("-r", payload) == b"abc\n"


def test_jq_join_output_preserves_exact_stdout():
    payload = {"exitCode": 0, "stdout": "abc", "stderr": ""}
    assert _jq_stdout("-j", payload) == b"abc"


def test_jq_join_output_preserves_embedded_newline_only():
    payload = {"exitCode": 0, "stdout": "line1\nline2", "stderr": ""}
    assert _jq_stdout("-j", payload) == b"line1\nline2"


def test_jq_join_output_empty_stdout():
    payload = {"exitCode": 0, "stdout": "", "stderr": ""}
    assert _jq_stdout("-j", payload) == b""
