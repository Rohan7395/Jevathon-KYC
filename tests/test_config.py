import pytest

import app.config as config
from app.models import ArticleDecision, Thresholds


def test_thresholds_defaults():
    t = Thresholds()
    assert t.same_person_clear == 0.25
    assert t.not_subject_clear == 0.20
    assert t.none_clear_conf == 0.725
    assert t.flag_same_person == 0.85


def test_article_decision_defaults():
    d = ArticleDecision(article_id="a", client_id="c")
    assert d.same_person is None
    assert d.is_subject is None
    assert d.risk_type is None
    assert d.risk_conf is None
    assert d.severity is None
    assert d.severity_conf is None
    assert d.risk_probs == {}
    assert d.error is None
    assert d.latency_ms == 0


def test_load_settings_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("JEV_BASE_URL", "https://x/")
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)

    with pytest.raises(RuntimeError) as exc_info:
        config.load_settings()

    assert "JEV_API_KEY" in str(exc_info.value)


def test_load_settings_strips_slash_and_defaults(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("JEV_BASE_URL", "https://api.typesafe.ai/")
    monkeypatch.setenv("JEV_API_KEY", "k")
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "")

    settings = config.load_settings()

    assert settings.jev_base_url == "https://api.typesafe.ai"
    assert settings.jev_model == "jev-latest"
    assert settings.llm_model is None
