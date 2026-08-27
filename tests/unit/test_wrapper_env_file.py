"""Tests for wrapper/aws's PAWS_ENV_FILE sourcing — mounted-dotenv token delivery.

Covers the block that sources $PAWS_ENV_FILE (or /run/paws/paws.env) into the
wrapper's own short-lived shell before profile resolution runs, so callers that
mount a dotenv file instead of setting PAWS_URL_*/PAWS_TOKEN_* directly in the
process environment still resolve correctly.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AWS_WRAPPER = REPO_ROOT / "wrapper" / "aws"


def _run_paws_version(env_overrides: dict, timeout: float = 5) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], **env_overrides}
    return subprocess.run(
        [str(AWS_WRAPPER), "--paws-version"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def test_env_file_supplies_profile_pair(tmp_path):
    """A readable PAWS_ENV_FILE resolves the profile even with no matching vars in the process env."""
    env_file = tmp_path / "paws.env"
    env_file.write_text('PAWS_URL_ACCT_A="http://127.0.0.1:1"\nPAWS_TOKEN_ACCT_A="tok-a"\n')

    result = _run_paws_version({"AWS_PROFILE": "acct-a", "PAWS_ENV_FILE": str(env_file)})

    # Resolution must have succeeded using the file — the only way to reach
    # the daemon-unreachable stage is past a successful resolve_paws_target.
    assert "no PAWS_URL_ACCT_A/PAWS_TOKEN_ACCT_A configured" not in result.stderr
    assert "daemon:  unreachable" in result.stderr


def test_missing_env_file_falls_through_to_process_env():
    """No file at PAWS_ENV_FILE (or default path) is not an error — plain env vars still work."""
    result = _run_paws_version(
        {
            "AWS_PROFILE": "acct-a",
            "PAWS_ENV_FILE": "/nonexistent/paws.env",
            "PAWS_URL_ACCT_A": "http://127.0.0.1:1",
            "PAWS_TOKEN_ACCT_A": "tok-a",
        }
    )

    assert "no PAWS_URL_ACCT_A/PAWS_TOKEN_ACCT_A configured" not in result.stderr
    assert "daemon:  unreachable" in result.stderr


def test_unconfigured_profile_still_fails_closed_with_env_file(tmp_path):
    """A present-but-unrelated env file must not mask a genuinely unconfigured profile."""
    env_file = tmp_path / "paws.env"
    env_file.write_text('PAWS_URL_ACCT_A="http://127.0.0.1:1"\nPAWS_TOKEN_ACCT_A="tok-a"\n')

    result = _run_paws_version({"AWS_PROFILE": "acct-z", "PAWS_ENV_FILE": str(env_file)})

    assert result.returncode == 1
    assert "no PAWS_URL_ACCT_Z/PAWS_TOKEN_ACCT_Z configured" in result.stderr
