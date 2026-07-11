import config


def test_positive_int_env_falls_back_for_malformed_and_nonpositive(monkeypatch):
    monkeypatch.setenv("PILOT_TEST_NUM_CTX", "garbage")
    assert config._positive_int_env("PILOT_TEST_NUM_CTX", 8192) == 8192
    monkeypatch.setenv("PILOT_TEST_NUM_CTX", "0")
    assert config._positive_int_env("PILOT_TEST_NUM_CTX", 8192) == 8192
    monkeypatch.setenv("PILOT_TEST_NUM_CTX", "-4")
    assert config._positive_int_env("PILOT_TEST_NUM_CTX", 8192) == 8192


def test_positive_int_env_accepts_positive_override(monkeypatch):
    monkeypatch.setenv("PILOT_TEST_NUM_CTX", "12288")
    assert config._positive_int_env("PILOT_TEST_NUM_CTX", 8192) == 12288
