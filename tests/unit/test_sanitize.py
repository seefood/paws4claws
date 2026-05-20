from paws import DEFAULT_ALLOWED_SERVICES, check_allowlist, check_file_io, validate_arg


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
    """Dollar signs are blocked to prevent variable expansion."""
    err = validate_arg("$HOME")
    assert err is not None
    assert "rejected" in err


def test_backtick_rejected():
    err = validate_arg("`id`")
    assert err is not None


def test_subshell_rejected():
    """$(...) subshell syntax is explicitly blocked."""
    err = validate_arg("$(id)")
    assert err is not None


def test_semicolon_rejected():
    err = validate_arg("foo;bar")
    assert err is not None


def test_pipe_rejected():
    err = validate_arg("foo|bar")
    assert err is not None


def test_path_traversal_rejected():
    """.. sequences are blocked to prevent path traversal."""
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
    """Services not in the default set produce a descriptive error message."""
    err = check_allowlist("kms", DEFAULT_ALLOWED_SERVICES)
    assert err == "paws: service 'kms' is not permitted"


def test_all_services_allowed_when_none():
    """allowed=None means PAWS_ALLOWED_SERVICES=all — every service is permitted."""
    assert check_allowlist("kms", None) is None
    assert check_allowlist("any-made-up-service", None) is None


def test_custom_allowlist():
    custom = frozenset({"kms", "s3"})
    assert check_allowlist("kms", custom) is None
    err = check_allowlist("ec2", custom)
    assert err is not None
    assert "ec2" in err


def test_s3_to_s3_cp_allowed():
    assert check_file_io(["s3", "cp", "s3://bucket/k1", "s3://bucket/k2"]) is None


def test_s3_to_stdout_allowed():
    assert check_file_io(["s3", "cp", "s3://bucket/key", "-"]) is None


def test_s3_mv_s3_to_s3_allowed():
    assert check_file_io(["s3", "mv", "s3://b/k1", "s3://b/k2"]) is None


def test_s3_sync_s3_to_s3_allowed():
    assert check_file_io(["s3", "sync", "s3://b/src/", "s3://b/dst/"]) is None


def test_local_dest_blocked():
    """Copying from S3 to a local path is rejected with a v1 limitation message."""
    err = check_file_io(["s3", "cp", "s3://bucket/key", "/tmp/file"])
    assert err is not None
    assert "not supported in v1" in err


def test_local_source_blocked():
    """Copying from a local path to S3 is also rejected in v1."""
    err = check_file_io(["s3", "cp", "/tmp/file", "s3://bucket/key"])
    assert err is not None
    assert "not supported in v1" in err


def test_local_sync_blocked():
    err = check_file_io(["s3", "sync", "./local", "s3://bucket/prefix"])
    assert err is not None


def test_s3_ls_not_affected():
    assert check_file_io(["s3", "ls", "s3://bucket/"]) is None


def test_non_s3_service_not_affected():
    assert check_file_io(["ec2", "cp", "/local/path"]) is None


def test_flags_before_paths_skipped():
    assert check_file_io(["s3", "cp", "--recursive", "s3://b/k1", "s3://b/k2"]) is None


def test_too_few_args_is_fine():
    """Calls with fewer than 2 args bypass the file-I/O guard entirely."""
    assert check_file_io(["s3"]) is None
    assert check_file_io([]) is None
