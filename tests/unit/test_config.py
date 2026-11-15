import pytest

from switchboard import config


def test_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nope.json")
    assert config.load()["servers"] == {}


def test_malformed_json_is_a_friendly_error(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    with pytest.raises(ValueError, match="not valid JSON"):
        config.load()


def test_non_object_config_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    with pytest.raises(ValueError, match="must be a JSON object"):
        config.load()


def test_resolve_expands_env_refs(monkeypatch):
    monkeypatch.setenv("SB_TEST_TOKEN", "secret")
    assert config.resolve("{env:SB_TEST_TOKEN}") == "secret"
    assert config.resolve("plain") == "plain"
    assert config.resolve({"a": "{env:SB_TEST_MISSING}"}) == {"a": ""}
