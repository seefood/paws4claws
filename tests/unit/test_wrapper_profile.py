"""Tests for wrapper/profile_resolve.sh — AWS_PROFILE -> PAWS_URL/PAWS_TOKEN routing."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_RESOLVE = REPO_ROOT / "wrapper" / "profile_resolve.sh"


def _resolve(**env_overrides) -> subprocess.CompletedProcess:
    """Run resolve_paws_target in a clean env (no host AWS_PROFILE/PAWS_URL_* leakage)."""
    script = f"""
. "{PROFILE_RESOLVE}"
if resolve_paws_target; then
    printf '%s\\n' "OK" "$PAWS_URL" "$PAWS_TOKEN"
else
    printf '%s\\n' "FAIL"
fi
"""
    env = {"PATH": os.environ["PATH"], **env_overrides}
    return subprocess.run(
        ["sh", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_no_profile_uses_bare_env_pair():
    result = _resolve(PAWS_URL="http://paws:7142", PAWS_TOKEN="tok-legacy")
    assert result.stdout == "OK\nhttp://paws:7142\ntok-legacy\n"


def test_no_profile_defaults_url_when_unset():
    result = _resolve(PAWS_TOKEN="tok-legacy")
    assert result.stdout == "OK\nhttp://paws:7142\ntok-legacy\n"


def test_profile_resolves_matching_pair():
    result = _resolve(
        AWS_PROFILE="acct-a",
        PAWS_URL_ACCT_A="http://paws-a:7142",
        PAWS_TOKEN_ACCT_A="tok-a",
    )
    assert result.stdout == "OK\nhttp://paws-a:7142\ntok-a\n"


def test_profile_name_is_uppercased_and_hyphens_become_underscores():
    result = _resolve(
        AWS_PROFILE="Acct-B",
        PAWS_URL_ACCT_B="http://paws-b:7142",
        PAWS_TOKEN_ACCT_B="tok-b",
    )
    assert result.stdout == "OK\nhttp://paws-b:7142\ntok-b\n"


def test_unconfigured_profile_fails_closed():
    """Error must name the requested profile and list what's configured, for debugging."""
    result = _resolve(
        AWS_PROFILE="acct-z",
        PAWS_URL_ACCT_A="http://paws-a:7142",
        PAWS_TOKEN_ACCT_A="tok-a",
    )
    assert result.stdout == "FAIL\n"
    assert "AWS_PROFILE=acct-z" in result.stderr
    assert "ACCT_A" in result.stderr


def test_profile_set_with_no_profiles_configured_fails_closed():
    result = _resolve(AWS_PROFILE="acct-z")
    assert result.stdout == "FAIL\n"
    assert "configured: none" in result.stderr


def test_profile_partially_configured_fails_closed():
    """Missing token half of the pair must not silently fall through."""
    result = _resolve(AWS_PROFILE="acct-a", PAWS_URL_ACCT_A="http://paws-a:7142")
    assert result.stdout == "FAIL\n"
