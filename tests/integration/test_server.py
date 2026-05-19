import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
from paws import make_handler

TOKENS = frozenset({"test-token-xyz"})
ALLOWED = frozenset({"s3", "sts"})


@pytest.fixture(scope="module")
def base_url():
    handler = make_handler(TOKENS, ALLOWED)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, json.loads(resp.read())


def _post(url, body, token=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ── /health ────────────────────────────────────────────────────────────────────


def test_health_no_auth(base_url):
    status, body = _get(f"{base_url}/health")
    assert status == 200
    assert body == {"ok": True}


# ── auth ───────────────────────────────────────────────────────────────────────


def test_missing_token_is_401(base_url):
    status, body = _post(f"{base_url}/invoke", {"args": ["sts", "get-caller-identity"]})
    assert status == 401
    assert body["error"] == "unauthorized"


def test_wrong_token_is_401(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["sts", "get-caller-identity"]},
        token="bad",
    )
    assert status == 401


def test_valid_token_proceeds(base_url):
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b'{"Account":"123"}'
    mock.stderr = b""
    with patch("paws.subprocess.run", return_value=mock):
        status, body = _post(
            f"{base_url}/invoke",
            {"args": ["sts", "get-caller-identity"]},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 0


# ── sanitization pipeline ──────────────────────────────────────────────────────


def test_unknown_service_is_403(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["kms", "list-keys"]},
        token="test-token-xyz",
    )
    assert status == 403
    assert "kms" in body["message"]


def test_allowed_services_none_permits_any_service():
    """PAWS_ALLOWED_SERVICES=all — allowed_services=None should skip the check."""
    handler = make_handler(TOKENS, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"

    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b"ok"
    mock.stderr = b""
    with patch("paws.subprocess.run", return_value=mock):
        status, body = _post(
            f"{url}/invoke",
            {"args": ["kms", "list-keys"]},
            token="test-token-xyz",
        )

    httpd.shutdown()
    assert status == 200
    assert body["exitCode"] == 0


def test_bad_arg_is_403(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["s3", "ls", "$(evil)"]},
        token="test-token-xyz",
    )
    assert status == 403
    assert body["error"] == "forbidden"


def test_local_file_cp_is_501(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["s3", "cp", "s3://bucket/key", "/tmp/file"]},
        token="test-token-xyz",
    )
    assert status == 501
    assert body["error"] == "not_implemented"
    assert "not supported in v1" in body["message"]


def test_s3_to_stdout_is_allowed(base_url):
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b"file content here"
    mock.stderr = b""
    with patch("paws.subprocess.run", return_value=mock):
        status, body = _post(
            f"{base_url}/invoke",
            {"args": ["s3", "cp", "s3://bucket/key", "-"]},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["stdout"] == "file content here"


# ── subprocess edge cases ──────────────────────────────────────────────────────


def test_command_timeout(base_url):
    with patch(
        "paws.subprocess.run", side_effect=subprocess.TimeoutExpired(["aws"], 120)
    ):
        status, body = _post(
            f"{base_url}/invoke",
            {"args": ["sts", "get-caller-identity"]},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 1
    assert "timed out" in body["stderr"]


def test_aws_not_found(base_url):
    with patch("paws.subprocess.run", side_effect=FileNotFoundError):
        status, body = _post(
            f"{base_url}/invoke",
            {"args": ["sts", "get-caller-identity"]},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 1
    assert "aws CLI not found" in body["stderr"]


def test_output_size_cap(base_url):
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b"x" * (10 * 1024 * 1024 + 1)
    mock.stderr = b""
    with patch("paws.subprocess.run", return_value=mock):
        status, body = _post(
            f"{base_url}/invoke",
            {"args": ["s3", "ls"]},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 1
    assert "truncated" in body["stderr"]


def test_aws_nonzero_exit_code_passes_through(base_url):
    mock = MagicMock()
    mock.returncode = 254
    mock.stdout = b""
    mock.stderr = b"An error occurred (NoSuchBucket)"
    with patch("paws.subprocess.run", return_value=mock):
        status, body = _post(
            f"{base_url}/invoke",
            {"args": ["s3", "ls", "s3://nonexistent-bucket-xyz"]},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 254
    assert "NoSuchBucket" in body["stderr"]


# ── malformed requests ─────────────────────────────────────────────────────────


def test_empty_args_is_400(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": []},
        token="test-token-xyz",
    )
    assert status == 400


def test_missing_args_key_is_400(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"wrong_key": "value"},
        token="test-token-xyz",
    )
    assert status == 400
