#!/usr/bin/env python3
"""PAWS — Proxied AWS Shell daemon."""

import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── Constants ──────────────────────────────────────────────────────────────────


DEFAULT_ALLOWED_SERVICES = frozenset(
    {
        "s3",
        "ec2",
        "logs",
        "ssm",
        "sts",
        "iam",
        "lambda",
        "cloudformation",
        "ecr",
        "secretsmanager",
    }
)
FILE_IO_SUBCOMMANDS = frozenset({"cp", "mv", "sync"})
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_STDIN_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
TIMEOUT_SECONDS = 120
PORT = int(os.environ.get("PAWS_PORT", "7142"))

_ARG_RE = re.compile(r"^[A-Za-z0-9:/_\-\.@=,*+%~\[\]{}]+$")
_BLOCKED_SEQS = ("$(", "..")
_BLOCKED_CHARS = frozenset("$`;\n\x00|&<>()\\ ")


# ── Startup ────────────────────────────────────────────────────────────────────


def load_tokens(env: dict[str, str] | None = None) -> frozenset[str]:
    """Read PAWS_TOKEN_<LABEL> env vars into a frozenset of token strings."""
    source = env if env is not None else os.environ
    return frozenset(v for k, v in source.items() if k.startswith("PAWS_TOKEN_") and v)


def load_allowed_services(env: dict[str, str] | None = None) -> frozenset[str] | None:
    """Parse PAWS_ALLOWED_SERVICES: 'all' → None (unrestricted), CSV → frozenset, unset → DEFAULT_ALLOWED_SERVICES."""
    source = env if env is not None else os.environ
    val = source.get("PAWS_ALLOWED_SERVICES", "").strip()
    if val.lower() == "all":
        return None
    if val:
        return frozenset(s.strip() for s in val.split(",") if s.strip())
    return DEFAULT_ALLOWED_SERVICES


# ── Sanitization ───────────────────────────────────────────────────────────────


def validate_arg(arg: str) -> str | None:
    """Return None if valid, error message if not."""
    if _ARG_RE.match(arg):
        for seq in _BLOCKED_SEQS:
            if seq in arg:
                return f"paws: argument rejected: '{arg}'"
        return None
    # JSON object/array values (e.g. --payload '{"key": "val"}') are safe
    # because subprocess.run uses shell=False — no shell expansion occurs.
    if arg.startswith(("{", "[")):
        try:
            json.loads(arg)
        except (ValueError, json.JSONDecodeError):
            return f"paws: argument rejected: '{arg}'"
        for seq in _BLOCKED_SEQS:
            if seq in arg:
                return f"paws: argument rejected: '{arg}'"
        return None
    return f"paws: argument rejected: '{arg}'"


def check_allowlist(service: str, allowed: frozenset[str] | None) -> str | None:
    """Return None if allowed, error message if not. None means all permitted."""
    if allowed is None:
        return None
    if service not in allowed:
        return f"paws: service '{service}' is not permitted"
    return None


def check_file_io(
    args: list[str],
    file_indices: frozenset[int] | None = None,
) -> str | None:
    """Return None if OK, error message if local file I/O detected without inline files."""
    if len(args) < 2 or args[0] != "s3" or args[1] not in FILE_IO_SUBCOMMANDS:
        return None
    covered = file_indices or frozenset()
    for i, arg in enumerate(args):
        if i < 2 or arg.startswith("--"):
            continue
        if not arg.startswith("s3://") and arg != "-":
            if i in covered:
                continue
            return (
                "paws: local file I/O is not supported without inline file content. "
                "Use S3-to-S3, S3-to-stdout (`-`), pipe data "
                "(`echo data | aws s3 cp - s3://bucket/key`), or pass the file via "
                "the v0.3 files payload. "
                "See https://github.com/seefood/paws4claws for the roadmap."
            )
    return None


def decode_stdin(raw: str | None) -> tuple[bytes | None, str | None]:
    """Decode optional base64 stdin field. Returns (bytes, error). None bytes = no stdin."""
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "stdin must be a string"
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None, "stdin must be valid base64"
    if len(data) > MAX_STDIN_BYTES:
        return None, f"paws: stdin exceeds {MAX_STDIN_BYTES} byte limit"
    return data, None


def decode_files(raw: list | None) -> tuple[list[tuple[int, bytes]], str | None]:
    """Decode optional files array. Returns ([(argIndex, bytes), ...], error)."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "files must be a list"
    seen: set[int] = set()
    result: list[tuple[int, bytes]] = []
    for item in raw:
        if not isinstance(item, dict):
            return [], "files entries must be objects"
        if "argIndex" not in item or "content" not in item:
            return [], "files entries require argIndex and content"
        idx = item["argIndex"]
        if not isinstance(idx, int) or idx < 0:
            return [], "argIndex must be a non-negative integer"
        if idx in seen:
            return [], f"duplicate argIndex in files: {idx}"
        seen.add(idx)
        content = item["content"]
        if not isinstance(content, str):
            return [], "files content must be a string"
        try:
            data = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            return [], f"files[{idx}] content must be valid base64"
        if len(data) > MAX_FILE_BYTES:
            return [], f"paws: file at argIndex {idx} exceeds {MAX_FILE_BYTES} byte limit"
        result.append((idx, data))
    return result, None


def _substitute_file_arg(original: str, temp_path: str) -> str:
    if original.startswith("fileb://"):
        return f"fileb://{temp_path}"
    if original.startswith("file://"):
        return f"file://{temp_path}"
    return temp_path


def materialize_files(
    args: list[str],
    files: list[tuple[int, bytes]],
) -> tuple[list[str], list[str], str | None]:
    """Write file blobs to temp paths and substitute argv. Returns (exec_args, temp_paths, error)."""
    if not files:
        return list(args), [], None
    exec_args = list(args)
    temp_paths: list[str] = []
    for idx, data in files:
        if idx >= len(exec_args):
            return exec_args, temp_paths, f"argIndex {idx} out of range"
        with tempfile.NamedTemporaryFile(prefix="paws-", delete=False) as handle:
            handle.write(data)
            path = handle.name
        temp_paths.append(path)
        exec_args[idx] = _substitute_file_arg(exec_args[idx], path)
    return exec_args, temp_paths, None


def cleanup_temp_files(paths: list[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── HTTP handler ───────────────────────────────────────────────────────────────


class PawsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the PAWS proxy daemon."""

    tokens: frozenset[str]
    allowed_services: frozenset[str] | None

    def log_message(self, fmt, *args):
        """Suppress default access log to avoid leaking token fragments."""

    def _send_json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        """Handle GET requests: only /health is supported."""
        if self.path == "/health":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        """Handle POST /invoke: authenticate, sanitize, and proxy to aws CLI."""
        if self.path != "/invoke":
            self._send_json(404, {"error": "not_found"})
            return

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
            self._send_json(401, {"error": "unauthorized"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            args = body["args"]
            if not isinstance(args, list) or not args:
                raise ValueError("args must be a non-empty list")
            args = [str(a) for a in args]
            stdin_raw = body.get("stdin")
            files_raw = body.get("files")
        except Exception as exc:
            self._send_json(400, {"error": "bad_request", "message": str(exc)})
            return

        stdin_bytes, err = decode_stdin(stdin_raw)
        if err:
            self._send_json(400, {"error": "bad_request", "message": err})
            return

        decoded_files, err = decode_files(files_raw)
        if err:
            self._send_json(400, {"error": "bad_request", "message": err})
            return

        file_indices = frozenset(idx for idx, _ in decoded_files)

        err = check_allowlist(args[0], self.allowed_services)
        if err:
            self._send_json(403, {"error": "forbidden", "message": err})
            return

        for arg in args:
            err = validate_arg(arg)
            if err:
                self._send_json(403, {"error": "forbidden", "message": err})
                return

        err = check_file_io(args, file_indices)
        if err:
            self._send_json(501, {"error": "not_implemented", "message": err})
            return

        exec_args, temp_paths, err = materialize_files(args, decoded_files)
        if err:
            cleanup_temp_files(temp_paths)
            self._send_json(400, {"error": "bad_request", "message": err})
            return

        for arg in exec_args:
            err = validate_arg(arg)
            if err:
                cleanup_temp_files(temp_paths)
                self._send_json(403, {"error": "forbidden", "message": err})
                return

        try:
            result = subprocess.run(
                ["aws", *exec_args],
                input=stdin_bytes,
                shell=False,
                capture_output=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._send_json(
                200,
                {
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": f"paws: command timed out after {TIMEOUT_SECONDS}s",
                },
            )
            return
        except FileNotFoundError:
            self._send_json(
                200,
                {
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": "paws: aws CLI not found in daemon container",
                },
            )
            return
        finally:
            cleanup_temp_files(temp_paths)

        stdout_too_big = len(result.stdout) > MAX_OUTPUT_BYTES
        stderr_too_big = len(result.stderr) > MAX_OUTPUT_BYTES
        if stdout_too_big or stderr_too_big:
            self._send_json(
                200,
                {
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": "paws: output truncated — exceeds 10 MB limit",
                },
            )
            return

        self._send_json(
            200,
            {
                "exitCode": result.returncode,
                "stdout": result.stdout.decode(errors="replace"),
                "stderr": result.stderr.decode(errors="replace"),
            },
        )


# ── Server factory ─────────────────────────────────────────────────────────────


def make_handler(
    tokens: frozenset[str],
    allowed_services: frozenset[str] | None,
) -> type[PawsHandler]:
    """Return a PawsHandler subclass with config baked in. Used in tests."""

    class _Handler(PawsHandler):
        pass

    _Handler.tokens = tokens
    _Handler.allowed_services = allowed_services
    return _Handler


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    """Start the PAWS daemon: validate environment, then serve forever."""
    if not shutil.which("aws"):
        print("paws: aws CLI not found in PATH — refusing to start", file=sys.stderr)
        sys.exit(1)

    tokens = load_tokens()
    if not tokens:
        print(
            "paws: no PAWS_TOKEN_* env vars configured — refusing to start",
            file=sys.stderr,
        )
        sys.exit(1)

    allowed_services = load_allowed_services()
    handler_class = make_handler(tokens, allowed_services)

    with ThreadingHTTPServer(("0.0.0.0", PORT), handler_class) as server:
        print(f"paws: listening on 0.0.0.0:{PORT}", file=sys.stderr, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
