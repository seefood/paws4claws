from paws import validate_arg


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
