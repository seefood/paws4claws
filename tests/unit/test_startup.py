from paws import DEFAULT_ALLOWED_SERVICES, load_allowed_services, load_tokens

# ── load_tokens ────────────────────────────────────────────────────────────────


def test_loads_single_token():
    env = {"PAWS_TOKEN_AGENT_A": "abc123"}
    assert load_tokens(env) == frozenset({"abc123"})


def test_loads_multiple_tokens():
    env = {"PAWS_TOKEN_A": "tok1", "PAWS_TOKEN_B": "tok2"}
    assert load_tokens(env) == frozenset({"tok1", "tok2"})


def test_ignores_non_prefixed_vars():
    env = {"PAWS_TOKEN_A": "good", "PAWS_SECRET": "ignored", "OTHER": "also"}
    assert load_tokens(env) == frozenset({"good"})


def test_empty_token_values_ignored():
    env = {"PAWS_TOKEN_A": "real", "PAWS_TOKEN_B": ""}
    assert load_tokens(env) == frozenset({"real"})


def test_no_tokens_returns_empty():
    assert load_tokens({}) == frozenset()


# ── load_allowed_services ──────────────────────────────────────────────────────


def test_defaults_when_unset():
    assert load_allowed_services({}) == DEFAULT_ALLOWED_SERVICES


def test_all_lowercase_returns_none():
    assert load_allowed_services({"PAWS_ALLOWED_SERVICES": "all"}) is None


def test_all_uppercase_returns_none():
    assert load_allowed_services({"PAWS_ALLOWED_SERVICES": "ALL"}) is None


def test_custom_comma_separated():
    result = load_allowed_services({"PAWS_ALLOWED_SERVICES": "s3,ec2"})
    assert result == frozenset({"s3", "ec2"})


def test_custom_with_spaces():
    result = load_allowed_services({"PAWS_ALLOWED_SERVICES": " s3 , ec2 "})
    assert result == frozenset({"s3", "ec2"})
