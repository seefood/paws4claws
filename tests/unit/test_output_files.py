import base64
import os
import tempfile

import pytest
from paws import (
    MAX_FILE_BYTES,
    check_file_io,
    classify_s3_file_slots,
    collect_output_files,
    prepare_output_paths,
)

from tests.output_commands import OUTPUT_COMMAND_CASES


def test_classify_s3_cp_download():
    inputs, outputs, err = classify_s3_file_slots(["s3", "cp", "s3://bucket/key", "./out.bin"])
    assert err is None
    assert inputs == frozenset()
    assert outputs == frozenset({3})


def test_classify_s3_cp_upload():
    inputs, outputs, err = classify_s3_file_slots(["s3", "cp", "./local.bin", "s3://bucket/key"])
    assert err is None
    assert inputs == frozenset({2})
    assert outputs == frozenset()


def test_classify_s3_mv_download():
    inputs, outputs, err = classify_s3_file_slots(["s3", "mv", "s3://bucket/key", "/tmp/out"])
    assert err is None
    assert outputs == frozenset({3})


def test_classify_recursive_with_local_returns_error():
    _, _, err = classify_s3_file_slots(["s3", "cp", "--recursive", "s3://bucket/prefix/", "./dir"])
    assert err is not None
    assert "recursive" in err


def test_check_file_io_allows_download_dest():
    args = ["s3", "cp", "s3://bucket/key", "./out.bin"]
    assert check_file_io(args) is None


def test_check_file_io_blocks_sync_local():
    err = check_file_io(["s3", "sync", "./local", "s3://bucket/prefix"])
    assert err is not None
    assert "sync" in err


def test_check_file_io_blocks_recursive_local():
    err = check_file_io(["s3", "cp", "--recursive", "s3://bucket/prefix/", "./dir"])
    assert err is not None
    assert "recursive" in err


def test_prepare_and_collect_output_files():
    args = ["s3", "cp", "s3://bucket/key", "./out.bin"]
    exec_args, temp_paths, slots, err = prepare_output_paths(args, frozenset({3}))
    assert err is None
    assert len(slots) == 1
    idx, path = slots[0]
    assert idx == 3
    assert exec_args[3] == path
    payload = b"exact-bytes"
    with open(path, "wb") as handle:
        handle.write(payload)
    output_files, out_err = collect_output_files(slots, 0)
    assert out_err is None
    assert len(output_files) == 1
    assert output_files[0]["argIndex"] == 3
    assert base64.b64decode(output_files[0]["content"]) == payload
    for p in temp_paths:
        os.unlink(p)


def test_collect_output_files_skipped_on_failure():
    slots = [(3, "/nonexistent/path")]
    output_files, err = collect_output_files(slots, 1)
    assert output_files == []
    assert err is None


def test_collect_output_files_oversized():
    with tempfile.NamedTemporaryFile(prefix="paws-", delete=False) as handle:
        handle.write(b"x" * (MAX_FILE_BYTES + 1))
        path = handle.name
    try:
        output_files, err = collect_output_files([(3, path)], 0)
        assert output_files == []
        assert "exceeds" in err
    finally:
        os.unlink(path)


@pytest.mark.parametrize("case", OUTPUT_COMMAND_CASES, ids=lambda c: c.id)
def test_output_command_shape(case):
    assert case.arg_index < len(case.args)
