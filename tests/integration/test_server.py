import base64
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import paws
import pytest
from paws import DEFAULT_ALLOWED_SERVICES, MAX_STDIN_BYTES, make_handler

REPO_ROOT = Path(__file__).resolve().parents[2]

from tests.file_commands import FILE_COMMAND_CASES
from tests.output_commands import OUTPUT_COMMAND_CASES
from tests.stdin_commands import STDIN_COMMAND_CASES, STDIN_COMMAND_REQUIRES_ALLOWLIST

TOKENS = frozenset({"test-token-xyz"})
ALLOWED = frozenset({"s3", "sts"})


@pytest.fixture(scope="module")
def base_url():
    """Spin up a real ThreadingHTTPServer on a random port for the module."""
    handler = make_handler(TOKENS, ALLOWED)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def all_services_url():
    """Server with the default PAWS service allowlist (all stdin-capable services)."""
    handler = make_handler(TOKENS, DEFAULT_ALLOWED_SERVICES)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def unrestricted_url():
    """Server with PAWS_ALLOWED_SERVICES=all (None allowlist)."""
    handler = make_handler(TOKENS, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url):
    """Issue a GET to url and return (status, parsed_body)."""
    with urllib.request.urlopen(url) as resp:  # nosec B310
        return resp.status, json.loads(resp.read())


def _post(url, body, token=None):
    """Issue a POST to url with JSON body and optional Bearer token."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:  # nosec B310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ── /health ────────────────────────────────────────────────────────────────────


def test_health_no_auth(base_url):
    """GET /health requires no auth and returns ok + version."""
    status, body = _get(f"{base_url}/health")
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == paws.VERSION


def test_wrapper_paws_version(base_url):
    """--paws-version prints wrapper and daemon versions (no PAWS_TOKEN required)."""
    wrapper = REPO_ROOT / "wrapper" / "aws"
    result = subprocess.run(
        [str(wrapper), "--paws-version"],
        env={**os.environ, "PAWS_URL": base_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"wrapper: {paws.VERSION}\ndaemon:  {paws.VERSION}\n"


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
    """A valid Bearer token reaches subprocess.run and returns exitCode 0."""
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


def test_s3_cp_download_allowed(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["s3", "cp", "s3://bucket/key", "./out.bin"]},
        token="test-token-xyz",
    )
    assert status != 501


def test_recursive_download_is_501(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {
            "args": [
                "s3",
                "cp",
                "--recursive",
                "s3://bucket/prefix/",
                "./dir",
            ]
        },
        token="test-token-xyz",
    )
    assert status == 501
    assert "recursive" in body["message"]


def test_sync_local_path_is_501(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["s3", "sync", "./local", "s3://bucket/prefix"]},
        token="test-token-xyz",
    )
    assert status == 501
    assert "sync" in body["message"]


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


def test_stdin_passthrough(base_url):
    """Optional stdin field is decoded and passed to subprocess.run(input=...)."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b"upload ok"
    mock.stderr = b""
    stdin_b64 = "aGVsbG8K"  # "hello\n"
    with patch("paws.subprocess.run", return_value=mock) as run_mock:
        status, body = _post(
            f"{base_url}/invoke",
            {"args": ["s3", "cp", "-", "s3://bucket/key"], "stdin": stdin_b64},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 0
    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["input"] == b"hello\n"


def test_invalid_stdin_is_400(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["s3", "cp", "-", "s3://bucket/key"], "stdin": "!!!bad!!!"},
        token="test-token-xyz",
    )
    assert status == 400
    assert body["error"] == "bad_request"
    assert "base64" in body["message"]


def test_stdin_size_cap_is_400(base_url):
    import base64

    oversized = base64.b64encode(b"x" * (MAX_STDIN_BYTES + 1)).decode()
    status, body = _post(
        f"{base_url}/invoke",
        {"args": ["s3", "cp", "-", "s3://bucket/key"], "stdin": oversized},
        token="test-token-xyz",
    )
    assert status == 400
    assert body["error"] == "bad_request"
    assert "exceeds" in body["message"]


@pytest.mark.parametrize("case", STDIN_COMMAND_CASES, ids=lambda c: c.id)
def test_stdin_command_reaches_subprocess(all_services_url, case):
    """Each documented stdin argv pattern passes sanitization and wires input= bytes."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b""
    mock.stderr = b""
    stdin_b64 = base64.b64encode(case.stdin_bytes).decode()
    with patch("paws.subprocess.run", return_value=mock) as run_mock:
        status, body = _post(
            f"{all_services_url}/invoke",
            {"args": case.args, "stdin": stdin_b64},
            token="test-token-xyz",
        )
    assert status == 200, body
    assert body["exitCode"] == 0
    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["input"] == case.stdin_bytes
    assert run_mock.call_args.args[0] == ["aws", *case.args]


@pytest.mark.parametrize("case", STDIN_COMMAND_REQUIRES_ALLOWLIST, ids=lambda c: c.id)
def test_stdin_command_extra_services_with_unrestricted_allowlist(unrestricted_url, case):
    """ecs/s3api stdin shapes reach subprocess when allowlist is unrestricted."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b""
    mock.stderr = b""
    stdin_b64 = base64.b64encode(case.stdin_bytes).decode()
    with patch("paws.subprocess.run", return_value=mock) as run_mock:
        status, body = _post(
            f"{unrestricted_url}/invoke",
            {"args": case.args, "stdin": stdin_b64},
            token="test-token-xyz",
        )
    assert status == 200, body
    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["input"] == case.stdin_bytes


@pytest.mark.parametrize("case", FILE_COMMAND_CASES, ids=lambda c: c.id)
def test_file_command_reaches_subprocess(all_services_url, case):
    """Inline files are materialized and substituted before subprocess.run."""
    args = list(case.args)
    args[case.arg_index] = "./upload.bin"
    file_b64 = base64.b64encode(case.file_bytes).decode()
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b""
    mock.stderr = b""
    with patch("paws.subprocess.run", return_value=mock) as run_mock:
        with patch("paws.cleanup_temp_files") as cleanup_mock:
            status, body = _post(
                f"{all_services_url}/invoke",
                {
                    "args": args,
                    "files": [{"argIndex": case.arg_index, "content": file_b64}],
                },
                token="test-token-xyz",
            )
    assert status == 200, body
    run_mock.assert_called_once()
    exec_argv = run_mock.call_args.args[0]
    assert exec_argv[0] == "aws"
    temp_path = exec_argv[case.arg_index + 1]
    assert os.path.basename(temp_path).startswith("paws-")
    cleanup_mock.assert_called_once()


@pytest.mark.parametrize("case", OUTPUT_COMMAND_CASES, ids=lambda c: c.id)
def test_output_command_returns_output_files(all_services_url, case):
    args = list(case.args)
    args[case.arg_index] = "./download.bin"

    def _fake_run(cmd, **kwargs):
        dest = cmd[case.arg_index + 1]
        with open(dest, "wb") as handle:
            handle.write(case.file_bytes)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = b""
        mock.stderr = b""
        return mock

    with patch("paws.subprocess.run", side_effect=_fake_run) as run_mock:
        status, body = _post(
            f"{all_services_url}/invoke",
            {"args": args},
            token="test-token-xyz",
        )
    assert status == 200, body
    assert body["exitCode"] == 0
    run_mock.assert_called_once()
    exec_argv = run_mock.call_args.args[0]
    assert os.path.basename(exec_argv[case.arg_index + 1]).startswith("paws-")
    assert "outputFiles" in body
    assert len(body["outputFiles"]) == 1
    entry = body["outputFiles"][0]
    assert entry["argIndex"] == case.arg_index
    assert base64.b64decode(entry["content"]) == case.file_bytes


def test_output_files_omitted_on_failure(all_services_url):
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = b""
    mock.stderr = b"failed"
    with patch("paws.subprocess.run", return_value=mock):
        status, body = _post(
            f"{all_services_url}/invoke",
            {"args": ["s3", "cp", "s3://bucket/key", "./out.bin"]},
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 1
    assert "outputFiles" not in body


def test_s3_cp_local_upload_allowed_with_files(base_url):
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = b""
    mock.stderr = b""
    content_b64 = base64.b64encode(b"payload").decode()
    with patch("paws.subprocess.run", return_value=mock) as run_mock:
        status, body = _post(
            f"{base_url}/invoke",
            {
                "args": ["s3", "cp", "./local.bin", "s3://bucket/key"],
                "files": [{"argIndex": 2, "content": content_b64}],
            },
            token="test-token-xyz",
        )
    assert status == 200
    assert body["exitCode"] == 0
    exec_argv = run_mock.call_args.args[0]
    assert os.path.basename(exec_argv[3]).startswith("paws-")


def test_invalid_files_is_400(base_url):
    status, body = _post(
        f"{base_url}/invoke",
        {
            "args": ["s3", "cp", "./local", "s3://bucket/key"],
            "files": [{"argIndex": 2, "content": "!!!bad!!!"}],
        },
        token="test-token-xyz",
    )
    assert status == 400
    assert body["error"] == "bad_request"


# ── subprocess edge cases ──────────────────────────────────────────────────────


def test_command_timeout(base_url):
    """TimeoutExpired is returned as exitCode 1 with a descriptive stderr."""
    with patch("paws.subprocess.run", side_effect=subprocess.TimeoutExpired(["aws"], 120)):
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
    """Output exceeding 10 MB is replaced with an error message."""
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
