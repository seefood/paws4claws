#!/usr/bin/env python3
"""PAWS — Proxied AWS Shell daemon."""

import json
import os
import re
import shutil
import subprocess
import sys
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
TIMEOUT_SECONDS = 120
PORT = int(os.environ.get("PAWS_PORT", "7142"))

_ARG_RE = re.compile(r"^[A-Za-z0-9:/_\-\.@=,*+%~]+$")
_BLOCKED_SEQS = ("$(", "..")
_BLOCKED_CHARS = frozenset("$`;\n\x00|&<>()\\ ")


# ── Startup ────────────────────────────────────────────────────────────────────


def load_tokens(env: dict[str, str] | None = None) -> frozenset[str]:
    """Read PAWS_TOKEN_<LABEL> env vars into a frozenset of token strings."""
    source = env if env is not None else os.environ
    return frozenset(v for k, v in source.items() if k.startswith("PAWS_TOKEN_") and v)


def load_allowed_services(env: dict[str, str] | None = None) -> frozenset[str] | None:
    """Return None to mean 'all services allowed'."""
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
    if not _ARG_RE.match(arg):
        return f"paws: argument rejected: '{arg}'"
    for seq in _BLOCKED_SEQS:
        if seq in arg:
            return f"paws: argument rejected: '{arg}'"
    if _BLOCKED_CHARS & set(arg):
        return f"paws: argument rejected: '{arg}'"
    return None


def check_allowlist(service: str, allowed: frozenset[str] | None) -> str | None:
    """Return None if allowed, error message if not. None means all permitted."""
    if allowed is None:
        return None
    if service not in allowed:
        return f"paws: service '{service}' is not permitted"
    return None


def check_file_io(args: list[str]) -> str | None:
    """Return None if OK, error message if local file I/O detected."""
    if len(args) < 2 or args[0] != "s3" or args[1] not in FILE_IO_SUBCOMMANDS:
        return None
    for arg in args[2:]:
        if arg.startswith("--"):
            continue
        if not arg.startswith("s3://") and arg != "-":
            return (
                "paws: local file I/O is not supported in v1. "
                "Only S3-to-S3 and S3-to-stdout transfers are allowed. "
                "See https://github.com/seefood/paws4claws for the roadmap."
            )
    return None


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
        except Exception as exc:
            self._send_json(400, {"error": "bad_request", "message": str(exc)})
            return

        err = check_allowlist(args[0], self.allowed_services)
        if err:
            self._send_json(403, {"error": "forbidden", "message": err})
            return

        for arg in args:
            err = validate_arg(arg)
            if err:
                self._send_json(403, {"error": "forbidden", "message": err})
                return

        err = check_file_io(args)
        if err:
            self._send_json(501, {"error": "not_implemented", "message": err})
            return

        try:
            result = subprocess.run(
                ["aws", *args],
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
