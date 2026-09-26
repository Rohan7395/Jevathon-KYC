import types

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.memo import MemoError, enforce_line_limit, generate_memo, select_memo_articles
from app.models import Article, ArticleDecision, Client, Receipts, ScreenRun, Verdict


def _article(id: str, **kw) -> Article:
    defaults = dict(client_id="c1", url=f"https://ex.com/{id}", title=f"Title {id}", title_only=False)
    defaults.update(kw)
    return Article(id=id, **defaults)


def _decision(article_id: str, **kw) -> ArticleDecision:
    defaults = dict(client_id="c1")
    defaults.update(kw)
    return ArticleDecision(article_id=article_id, **defaults)


def test_select_memo_articles_order_and_limit():
    articles = [_article("A"), _article("B"), _article("C"), _article("D")]
    decisions = {
        "C": _decision("C", severity=1, same_person=0.8),
        "D": _decision("D", severity=3, same_person=0.9),
        "B": _decision("B", severity=None),
    }
    verdicts = {
        "A": (Verdict.CLEAR, "different person"),
        "B": (Verdict.REVIEW, "uncertain"),
        "C": (Verdict.HIT, "fraud, severity 1"),
        "D": (Verdict.HIT, "fraud, severity 3"),
    }

    items = select_memo_articles(articles, decisions, verdicts)
    assert [item[0].id for item in items] == ["D", "C", "B"]

    limited = select_memo_articles(articles, decisions, verdicts, limit=2)
    assert [item[0].id for item in limited] == ["D", "C"]


def _fake_llm(content, captured=None):
    def create(**kwargs):
        if captured is not None:
            captured["messages"] = kwargs.get("messages")
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
        )
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def test_generate_memo_truncates_and_cites_selected_urls_only():
    client = Client(id="c1", name="Michael Jordan")
    articles = [
        _article("clear", title="Not relevant"),
        _article("hit", title="Michael Jordan charged with fraud"),
    ]
    decisions = {
        "hit": _decision("hit", same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2),
    }
    verdicts = {
        "clear": (Verdict.CLEAR, "different person"),
        "hit": (Verdict.HIT, "fraud, severity 2"),
    }

    content = "line1\n\nline2\nline3\n\nline4\nline5\nline6\nline7\nline8\n"
    captured = {}
    memo = generate_memo(client, articles, decisions, verdicts, llm=_fake_llm(content, captured), model="test-model")

    assert memo.split("\n") == ["line1", "line2", "line3", "line4", "line5", "line6"]

    all_content = " ".join(m["content"] for m in captured["messages"])
    assert "https://ex.com/hit" in all_content
    assert "https://ex.com/clear" not in all_content


def test_generate_memo_wraps_call_failure_and_empty_response():
    client = Client(id="c1", name="Michael Jordan")
    articles = [_article("hit", title="Michael Jordan charged with fraud")]
    decisions = {
        "hit": _decision("hit", same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2),
    }
    verdicts = {"hit": (Verdict.HIT, "fraud, severity 2")}

    def boom(**kwargs):
        raise RuntimeError("boom")

    failing_llm = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=boom)))
    with pytest.raises(MemoError) as exc_info:
        generate_memo(client, articles, decisions, verdicts, llm=failing_llm, model="test-model")
    assert str(exc_info.value).startswith("llm_call_failed")

    with pytest.raises(MemoError, match="^empty_llm_response$"):
        generate_memo(client, articles, decisions, verdicts, llm=_fake_llm(None), model="test-model")


def test_enforce_line_limit():
    assert enforce_line_limit("a\n\nb\nc\n\n", max_lines=2) == "a\nb"


def _screen_run() -> ScreenRun:
    clients = [
        Client(id="c1", name="Jane Doe"),
        Client(id="c2", name="John Clean"),
    ]
    articles = [
        _article("a1", client_id="c1", title="Jane Doe charged with fraud"),
        _article("a2", client_id="c2", title="John Clean profile"),
    ]
    decisions = [
        _decision("a1", client_id="c1", same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2),
        _decision("a2", client_id="c2", same_person=0.05, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2),
    ]
    return ScreenRun(clients=clients, articles=articles, decisions=decisions, receipts=Receipts())


@pytest.fixture
def memo_client(monkeypatch):
    run = _screen_run()
    monkeypatch.setattr(api, "get_run", lambda: run)
    api._memo_cache.clear()
    return TestClient(api.app)


def test_memo_endpoint_caches_and_calls_stub_once(memo_client, monkeypatch):
    calls = {"n": 0}

    def fake_generate_memo(client, articles, decisions, verdicts, llm=None, model=None):
        calls["n"] += 1
        return "line1\nline2"

    monkeypatch.setattr(api, "generate_memo", fake_generate_memo)

    resp1 = memo_client.post("/api/memo/c1")
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1["cached"] is False
    assert body1["memo"] == "line1\nline2"

    resp2 = memo_client.post("/api/memo/c1")
    assert resp2.status_code == 200
    assert resp2.json()["cached"] is True

    assert calls["n"] == 1


def test_memo_endpoint_errors(memo_client, monkeypatch):
    resp_clear = memo_client.post("/api/memo/c2")
    assert resp_clear.status_code == 409

    resp_missing = memo_client.post("/api/memo/nope")
    assert resp_missing.status_code == 404

    def fake_generate_memo_error(client, articles, decisions, verdicts, llm=None, model=None):
        raise MemoError("x")

    monkeypatch.setattr(api, "generate_memo", fake_generate_memo_error)
    resp_error = memo_client.post("/api/memo/c1")
    assert resp_error.status_code == 502
    assert resp_error.json()["detail"] == "x"
