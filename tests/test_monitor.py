import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.gdelt import article_id_for, fetch_new_articles
from app.models import Article, ArticleDecision, Client, Receipts, ScreenRun


async def test_fetch_new_articles_filters_and_maps():
    client = Client(id="c1", name="Jane Doe")
    known_id_a = article_id_for("c1", "https://x.com/1")

    def handler(request):
        return httpx.Response(200, json={
            "articles": [
                {"url": "https://x.com/1", "title": "Jane Doe charged", "seendate": "x", "domain": "x.com", "language": "en"},
                {"url": "https://x.com/3", "title": "Unrelated story", "seendate": "x", "domain": "x.com", "language": "en"},
                {"url": "https://x.com/2", "title": "DOE firm raided", "seendate": "20260926T120000Z", "domain": "x.com", "language": "en"},
            ]
        })

    sleep = AsyncMock()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        new_articles, fetch_failures = await fetch_new_articles([client], {known_id_a}, http, sleep=sleep)

    assert fetch_failures == []
    assert len(new_articles) == 1
    a = new_articles[0]
    assert a.id == article_id_for("c1", "https://x.com/2")
    assert a.title_only is True
    assert a.text is None
    assert a.seen_date == "20260926T120000Z"


async def test_fetch_new_articles_handles_non_json(caplog):
    c1 = Client(id="c1", name="Jane Doe")
    c2 = Client(id="c2", name="John Smith")

    def handler(request):
        query = request.url.params.get("query", "")
        if "Jane Doe" in query:
            return httpx.Response(200, text="Please limit requests")
        return httpx.Response(200, json={"articles": []})

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            new_articles, fetch_failures = await fetch_new_articles(
                [c1, c2], set(), http, sleep=fake_sleep,
            )

    assert new_articles == []
    assert fetch_failures == ["c1"]
    assert sleep_calls == [10, 5]
    assert any("Please limit requests" in rec.message for rec in caplog.records)


def _judge_decision(article_id: str, client_id: str, **kw) -> ArticleDecision:
    defaults = dict(
        same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2, severity_conf=0.8,
    )
    defaults.update(kw)
    return ArticleDecision(article_id=article_id, client_id=client_id, **defaults)


async def test_screen_new_articles_merges_and_computes_receipts():
    from app.screening import screen_new_articles

    client = Client(id="c1", name="Jane Doe")
    existing_article = Article(id="e1", client_id="c1", url="https://ex.com/e1", title="Existing", title_only=True)
    existing_decision = _judge_decision("e1", "c1")
    run = ScreenRun(
        clients=[client],
        articles=[existing_article],
        decisions=[existing_decision],
        receipts=Receipts(jev_calls=5, input_tokens=1000, llm_equiv_cost_usd=0.5),
    )

    n1 = Article(id="n1", client_id="c1", url="https://ex.com/n1", title="New 1", title_only=True)
    n2 = Article(id="n2", client_id="c1", url="https://ex.com/n2", title="New 2", title_only=True)

    async def judge(c, a):
        if a.id == "n1":
            return _judge_decision("n1", "c1", input_tokens=400)
        raise RuntimeError("boom")

    on_decision_calls: list[ArticleDecision] = []

    merged, new_decisions, tick_receipts = await screen_new_articles(
        run, [n1, n2], judge, on_decision_calls.append,
    )

    assert len(merged.articles) == 3
    assert len(merged.decisions) == 3
    second = next(d for d in new_decisions if d.article_id == "n2")
    assert second.error == "exception:RuntimeError"

    assert tick_receipts.jev_calls == 2
    assert tick_receipts.jev_errors == 1
    assert tick_receipts.input_tokens == 400
    assert tick_receipts.jev_cost_usd == pytest.approx(400 * 4.2e-8)
    assert tick_receipts.llm_equiv_cost_usd == pytest.approx(0.2)

    assert merged.receipts.jev_calls == 7
    assert len(on_decision_calls) == 2
    assert len(run.articles) == 1


async def test_screen_new_articles_noop_when_nothing_new():
    client = Client(id="c1", name="Jane Doe")
    run = ScreenRun(clients=[client], articles=[], decisions=[], receipts=Receipts())

    from app.screening import screen_new_articles

    judge_calls = []

    async def judge(c, a):
        judge_calls.append(a)
        raise AssertionError("judge should not be called")

    merged, new_decisions, tick_receipts = await screen_new_articles(run, [], judge, lambda d: None)

    assert merged is run
    assert new_decisions == []
    assert tick_receipts == Receipts()
    assert judge_calls == []


def _fixture_run(n_clients: int = 2) -> ScreenRun:
    clients = [Client(id=f"c{i}", name=f"Person {i}") for i in range(1, n_clients + 1)]
    return ScreenRun(clients=clients, articles=[], decisions=[], receipts=Receipts())


def test_monitor_tick_end_to_end(monkeypatch):
    run = _fixture_run()
    api.set_run(run)

    monkeypatch.setattr(api, "_persist_run", lambda r: None)

    audit_calls = []

    def fake_append_decisions(path, run_id, decisions, verdicts, strictness):
        audit_calls.append(decisions)
        return len(decisions)

    monkeypatch.setattr(api, "append_decisions", fake_append_decisions)

    article_a = Article(id="aA", client_id="c1", url="https://ex.com/a", title="Person 1 fraud", title_only=True)
    article_b = Article(id="aB", client_id="c1", url="https://ex.com/b", title="Person 1 clean", title_only=True)

    async def fake_monitor_fetch(clients, known_ids):
        return [article_a, article_b], []

    monkeypatch.setattr(api, "_monitor_fetch", fake_monitor_fetch)

    async def fake_monitor_judge(client, article):
        if article.id == "aA":
            return ArticleDecision(
                article_id="aA", client_id=client.id,
                same_person=0.95, is_subject=0.9, risk_type="fraud", risk_conf=0.9,
                severity=2, severity_conf=0.8, risk_probs={"fraud": 0.9},
            )
        return ArticleDecision(
            article_id="aB", client_id=client.id,
            same_person=0.1, is_subject=0.1, risk_type="none", risk_conf=0.9,
            severity=0, severity_conf=0.9,
        )

    monkeypatch.setattr(api, "_monitor_judge", fake_monitor_judge)

    client = TestClient(api.app)
    resp = client.post("/api/monitor/tick")
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_articles"] == 2
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["verdict"] == "HIT"
    assert body["alerts"][0]["reason"] == "fraud, severity 2"

    resp_results = client.get("/api/results")
    assert resp_results.json()["receipts"]["jev_calls"] == body["tick_receipts"]["jev_calls"] == 2

    assert len(audit_calls) == 2


def test_monitor_tick_idempotent_and_scoping(monkeypatch):
    run = _fixture_run()
    api.set_run(run)

    monkeypatch.setattr(api, "_persist_run", lambda r: None)
    monkeypatch.setattr(api, "append_decisions", lambda *a, **kw: 0)

    article_a = Article(id="aA", client_id="c1", url="https://ex.com/a", title="Person 1 fraud", title_only=True)
    article_b = Article(id="aB", client_id="c1", url="https://ex.com/b", title="Person 1 clean", title_only=True)
    all_articles = [article_a, article_b]

    async def fake_monitor_fetch(clients, known_ids):
        remaining = [a for a in all_articles if a.id not in known_ids]
        return remaining, []

    monkeypatch.setattr(api, "_monitor_fetch", fake_monitor_fetch)

    async def fake_monitor_judge(client, article):
        if article.id == "aA":
            return ArticleDecision(
                article_id="aA", client_id=client.id,
                same_person=0.95, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2, severity_conf=0.8,
            )
        return ArticleDecision(
            article_id="aB", client_id=client.id,
            same_person=0.1, is_subject=0.1, risk_type="none", risk_conf=0.9, severity=0, severity_conf=0.9,
        )

    monkeypatch.setattr(api, "_monitor_judge", fake_monitor_judge)

    client = TestClient(api.app)

    resp1 = client.post("/api/monitor/tick")
    assert resp1.status_code == 200
    first_total = resp1.json()["receipts"]["jev_calls"]

    resp2 = client.post("/api/monitor/tick")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["new_articles"] == 0
    assert body2["alerts"] == []
    assert body2["receipts"]["jev_calls"] == first_total

    resp_bad = client.post("/api/monitor/tick", params={"client_ids": "zzz"})
    assert resp_bad.status_code == 400
