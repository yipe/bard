from troubadour import config, settings


def test_saved_models_override_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert settings.current()["whisper"] == config.WHISPER_MODEL
    chosen = settings.update({"whisper": settings.WHISPER_LARGE, "memory": "other/gemma"})
    assert chosen["whisper"] == settings.WHISPER_LARGE
    assert chosen["memory"] == "other/gemma"
    assert chosen["llm"] == config.LLM_MODEL
    again = settings.current()
    assert again["whisper"] == settings.WHISPER_LARGE
    assert "other/gemma" in settings.known_llms()
    assert config.LLM_MODEL in settings.known_llms()


def test_address_accepts_a_port_a_lan_host_or_openrouter():
    assert settings.normalize_llm_url("11434") == "http://127.0.0.1:11434/v1"
    assert settings.normalize_llm_url("10.0.0.8:1234") == "http://10.0.0.8:1234/v1"
    assert settings.normalize_llm_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1"
    try:
        settings.normalize_llm_url("file:///etc/passwd")
    except ValueError:
        pass
    else:
        raise AssertionError("a file URL should be rejected")
