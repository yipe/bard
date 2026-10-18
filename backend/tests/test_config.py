from troubadour import config


def test_env_falls_back_to_the_pre_rename_name(monkeypatch):
    monkeypatch.delenv("TROUBADOUR_PORT", raising=False)
    monkeypatch.setenv("BARD_PORT", "9999")
    assert config.env("PORT") == "9999"


def test_env_prefers_the_new_name_and_then_the_default(monkeypatch):
    monkeypatch.setenv("BARD_PORT", "9999")
    monkeypatch.setenv("TROUBADOUR_PORT", "7777")
    assert config.env("PORT") == "7777"
    monkeypatch.delenv("TROUBADOUR_PORT")
    monkeypatch.delenv("BARD_PORT")
    assert config.env("PORT", "8765") == "8765"
    assert config.env("PORT") is None
