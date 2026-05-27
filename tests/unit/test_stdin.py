import base64

from paws import MAX_STDIN_BYTES, decode_stdin


def test_decode_stdin_none():
    data, err = decode_stdin(None)
    assert data is None
    assert err is None


def test_decode_stdin_valid_roundtrip():
    raw = base64.b64encode(b"hello\n").decode()
    data, err = decode_stdin(raw)
    assert err is None
    assert data == b"hello\n"


def test_decode_stdin_empty_string():
    data, err = decode_stdin("")
    assert err is None
    assert data == b""


def test_decode_stdin_invalid_base64():
    data, err = decode_stdin("not!!!valid")
    assert data is None
    assert err == "stdin must be valid base64"


def test_decode_stdin_non_string():
    data, err = decode_stdin(123)  # type: ignore[arg-type]
    assert data is None
    assert err == "stdin must be a string"


def test_decode_stdin_oversized():
    oversized = base64.b64encode(b"x" * (MAX_STDIN_BYTES + 1)).decode()
    data, err = decode_stdin(oversized)
    assert data is None
    assert "exceeds" in err
