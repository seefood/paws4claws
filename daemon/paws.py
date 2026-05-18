#!/usr/bin/env python3
"""PAWS — Proxied AWS Shell daemon."""

import os
import re

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
