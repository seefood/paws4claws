import base64

import pytest
from paws import (
    MAX_FILE_BYTES,
    check_file_io,
    cleanup_temp_files,
    decode_files,
    materialize_files,
)

from tests.file_commands import FILE_COMMAND_CASES


def test_decode_files_none():
    files, err = decode_files(None)
    assert files == []
    assert err is None


def test_decode_files_valid():
    raw = [{"argIndex": 2, "content": base64.b64encode(b"abc").decode()}]
    files, err = decode_files(raw)
    assert err is None
    assert files == [(2, b"abc")]


def test_decode_files_invalid_base64():
    files, err = decode_files([{"argIndex": 0, "content": "!!!"}])
    assert files == []
    assert "base64" in err


def test_decode_files_duplicate_arg_index():
    raw = [
        {"argIndex": 1, "content": base64.b64encode(b"a").decode()},
        {"argIndex": 1, "content": base64.b64encode(b"b").decode()},
    ]
    files, err = decode_files(raw)
    assert "duplicate" in err


def test_decode_files_oversized():
    oversized = base64.b64encode(b"x" * (MAX_FILE_BYTES + 1)).decode()
    files, err = decode_files([{"argIndex": 0, "content": oversized}])
    assert "exceeds" in err


def test_decode_files_out_of_range_on_materialize():
    exec_args, temp_paths, err = materialize_files(["s3", "ls"], [(5, b"x")])
    assert err is not None
    assert temp_paths == []


def test_materialize_files_preserves_exact_bytes():
    args = ["s3", "cp", "./local.zip", "s3://bucket/key"]
    data = b"no-newline-at-end"
    exec_args, temp_paths, err = materialize_files(args, [(2, data)])
    assert err is None
    assert len(temp_paths) == 1
    assert exec_args[2] == temp_paths[0]
    with open(temp_paths[0], "rb") as handle:
        assert handle.read() == data
    cleanup_temp_files(temp_paths)


def test_materialize_files_file_uri_substitution():
    args = ["lambda", "update-function-code", "--zip-file", "fileb://./bundle.zip"]
    exec_args, temp_paths, err = materialize_files(args, [(3, b"zip")])
    assert err is None
    assert exec_args[3] == f"fileb://{temp_paths[0]}"
    cleanup_temp_files(temp_paths)


def test_materialize_files_file_scheme_substitution():
    args = ["iam", "create-policy", "--policy-document", "file://./policy.json"]
    exec_args, temp_paths, err = materialize_files(args, [(3, b"{}")])
    assert err is None
    assert exec_args[3] == f"file://{temp_paths[0]}"
    cleanup_temp_files(temp_paths)


def test_check_file_io_allows_covered_local_path():
    args = ["s3", "cp", "./local", "s3://bucket/key"]
    assert check_file_io(args, frozenset({2})) is None


def test_check_file_io_blocks_uncovered_local_path():
    args = ["s3", "cp", "./local", "s3://bucket/key"]
    err = check_file_io(args)
    assert err is not None
    assert "inline file content" in err


def test_check_file_io_blocks_partial_coverage():
    args = ["s3", "cp", "./src", "./dst"]
    err = check_file_io(args, frozenset({2}))
    assert err is not None


@pytest.mark.parametrize("case", FILE_COMMAND_CASES, ids=lambda c: c.id)
def test_file_command_args_pass_sanitize_shape(case):
    assert case.arg_index < len(case.args)
