from paws import DEFAULT_ALLOWED_SERVICES, check_allowlist, validate_arg


def test_plain_service_name():
    assert validate_arg("s3") is None


def test_s3_uri():
    assert validate_arg("s3://my-bucket/prefix/key.gz") is None


def test_flag_with_value():
    assert validate_arg("--output") is None
    assert validate_arg("--region") is None
    assert validate_arg("us-east-1") is None


def test_equals_in_arg():
    assert validate_arg("Name=tag:Env,Values=prod") is None


def test_dollar_sign_rejected():
    err = validate_arg("$HOME")
    assert err is not None
    assert "rejected" in err


def test_backtick_rejected():
    err = validate_arg("`id`")
    assert err is not None


def test_subshell_rejected():
    err = validate_arg("$(id)")
    assert err is not None


def test_semicolon_rejected():
    err = validate_arg("foo;bar")
    assert err is not None


def test_pipe_rejected():
    err = validate_arg("foo|bar")
    assert err is not None


def test_path_traversal_rejected():
    err = validate_arg("../../etc/passwd")
    assert err is not None
    assert "rejected" in err


def test_newline_rejected():
    err = validate_arg("foo\nbar")
    assert err is not None


def test_nul_rejected():
    err = validate_arg("foo\x00bar")
    assert err is not None


def test_default_service_passes():
    assert check_allowlist("s3", DEFAULT_ALLOWED_SERVICES) is None
    assert check_allowlist("sts", DEFAULT_ALLOWED_SERVICES) is None


def test_unknown_service_blocked():
    err = check_allowlist("kms", DEFAULT_ALLOWED_SERVICES)
    assert err == "paws: service 'kms' is not permitted"


def test_all_services_allowed_when_none():
    assert check_allowlist("kms", None) is None
    assert check_allowlist("any-made-up-service", None) is None


def test_custom_allowlist():
    custom = frozenset({"kms", "s3"})
    assert check_allowlist("kms", custom) is None
    err = check_allowlist("ec2", custom)
    assert err is not None
    assert "ec2" in err
