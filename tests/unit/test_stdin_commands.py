import pytest
from paws import DEFAULT_ALLOWED_SERVICES, check_allowlist, check_file_io, validate_arg

from tests.stdin_commands import STDIN_COMMAND_CASES, STDIN_COMMAND_REQUIRES_ALLOWLIST


@pytest.mark.parametrize("case", STDIN_COMMAND_CASES, ids=lambda c: c.id)
def test_stdin_command_args_pass_default_allowlist(case):
    err = check_allowlist(case.service, DEFAULT_ALLOWED_SERVICES)
    assert err is None, err


@pytest.mark.parametrize("case", STDIN_COMMAND_CASES, ids=lambda c: c.id)
def test_stdin_command_args_pass_validate_arg(case):
    for arg in case.args:
        err = validate_arg(arg)
        assert err is None, f"{case.id} arg {arg!r}: {err}"


@pytest.mark.parametrize("case", STDIN_COMMAND_CASES, ids=lambda c: c.id)
def test_stdin_command_args_pass_file_io_guard(case):
    err = check_file_io(case.args)
    assert err is None, err


@pytest.mark.parametrize("case", STDIN_COMMAND_REQUIRES_ALLOWLIST, ids=lambda c: c.id)
def test_stdin_command_extra_services_blocked_by_default_allowlist(case):
    err = check_allowlist(case.service, DEFAULT_ALLOWED_SERVICES)
    assert err is not None
    assert case.service in err


@pytest.mark.parametrize("case", STDIN_COMMAND_REQUIRES_ALLOWLIST, ids=lambda c: c.id)
def test_stdin_command_extra_services_args_still_sanitize(case):
    for arg in case.args:
        err = validate_arg(arg)
        assert err is None, f"{case.id} arg {arg!r}: {err}"
