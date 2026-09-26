from subprocess import CompletedProcess

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.models import Article, ArticleDecision, Client, Receipts, ScreenRun


def _client(id: str, name: str, **kw) -> Client:
    return Client(id=id, name=name, **kw)


def _article(id: str, client_id: str, **kw) -> Article:
    defaults = dict(
        url=f"https://ex.com/{id}",
        title=f"Title {id}",
        domain="ex.com",
        text=None,
        title_only=True,
    )
    defaults.update(kw)
    return Article(id=id, client_id=client_id, **defaults)


def _decision(article_id: str, client_id: str, **kw) -> ArticleDecision:
    return ArticleDecision(article_id=article_id, client_id=client_id, **kw)


@pytest.fixture
def sample_run() -> ScreenRun:
    clients = [
        _client("c1", "Jane Doe"),
        _client("c2", "Michael Jordan", birth_year=1982, city="Columbus", occupation="dentist"),
        _client("c3", "John Smith", birth_year=1980, city="Denver", occupation="software engineer"),
        _client("c4", "Ana Ruiz"),
        _client("c5", "Wei Chen"),
    ]
    articles = [
        _article("a1", "c1"),
        _article("a2", "c2"),
        _article("a3", "c3"),
        _article("a5", "c5"),
    ]
    decisions = [
        _decision("a1", "c1", same_person=0.95, is_subject=0.90, risk_type="fraud", risk_conf=0.90, severity=2),
        _decision("a2", "c2", same_person=0.05, is_subject=0.90, risk_type="fraud", risk_conf=0.80, severity=3),
        _decision("a3", "c3", same_person=0.20, is_subject=0.50, risk_type="none", risk_conf=0.60, severity=0),
        _decision("a5", "c5", error="timeout"),
    ]
    receipts = Receipts(
        jev_calls=4, jev_errors=1, input_tokens=1600,
        jev_cost_usd=0.0000672, elapsed_s=3.2, llm_equiv_cost_usd=0.012,
    )
    return ScreenRun(clients=clients, articles=articles, decisions=decisions, receipts=receipts)


@pytest.fixture
def api_client(tmp_path, monkeypatch, sample_run) -> TestClient:
    results_path = tmp_path / "results.json"
    results_path.write_text(sample_run.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(api, "RESULTS_PATH", results_path)
    api.reset_cache()
    return TestClient(api.app)


def test_results_default(api_client):
    resp = api_client.get("/api/results")
    assert resp.status_code == 200
    body = resp.json()

    assert body["counts"] == {"CLEAR": 2, "REVIEW": 1, "FLAGGED": 1, "NO_COVERAGE": 1}

    by_id = {c["id"]: c for c in body["clients"]}
    assert by_id["c1"]["status"] == "FLAGGED"
    assert by_id["c1"]["reason"] == "fraud, severity 2"
    assert by_id["c2"]["status"] == "CLEAR"
    assert by_id["c2"]["reason"] == "different person"
    assert by_id["c4"]["status"] == "NO_COVERAGE"
    assert by_id["c5"]["status"] == "REVIEW"
    assert by_id["c5"]["reason"].startswith("fail-closed")

    assert body["receipts"]["jev_calls"] == 4

    assert [c["id"] for c in body["clients"]] == ["c1", "c2", "c3", "c4", "c5"]


def test_dial_reroutes_without_jev(api_client, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("must not call Jev")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)

    resp0 = api_client.get("/api/results", params={"strictness": 0.0})
    assert resp0.json()["counts"] == {"CLEAR": 2, "REVIEW": 1, "FLAGGED": 1, "NO_COVERAGE": 1}

    resp1 = api_client.get("/api/results", params={"strictness": 1.0})
    body1 = resp1.json()
    assert body1["counts"] == {"CLEAR": 1, "REVIEW": 2, "FLAGGED": 1, "NO_COVERAGE": 1}
    by_id1 = {c["id"]: c for c in body1["clients"]}
    assert by_id1["c3"]["status"] == "REVIEW"
    assert by_id1["c3"]["reason"] == "uncertain"

    resp_over = api_client.get("/api/results", params={"strictness": 5})
    body_over = resp_over.json()
    assert body_over["strictness"] == 1.0
    assert body_over["counts"] == body1["counts"]

    assert resp0.json()["receipts"] == body1["receipts"] == body_over["receipts"]


def test_client_detail(api_client):
    resp = api_client.get("/api/client/c2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["articles"][0]["verdict"] == "CLEAR"
    assert body["articles"][0]["reason"] == "different person"
    assert body["articles"][0]["decision"]["same_person"] == 0.05
    assert body["articles"][0]["title_only"] is True

    resp3 = api_client.get("/api/client/c3", params={"strictness": 1.0})
    assert resp3.json()["articles"][0]["verdict"] == "REVIEW"

    resp404 = api_client.get("/api/client/nope")
    assert resp404.status_code == 404


def test_missing_results_and_index(api_client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RESULTS_PATH", tmp_path / "does_not_exist.json")
    api.reset_cache()

    resp = api_client.get("/api/results")
    assert resp.status_code == 503
    assert "run_screen.py" in resp.json()["detail"]

    resp_index = api_client.get("/")
    assert resp_index.status_code == 200
    assert resp_index.headers["content-type"].startswith("text/html")


def test_screen_reload_and_rescreen(api_client, tmp_path, sample_run, monkeypatch):
    results_path = tmp_path / "results.json"

    updated9 = sample_run.model_copy(update={"receipts": sample_run.receipts.model_copy(update={"jev_calls": 9})})
    results_path.write_text(updated9.model_dump_json(), encoding="utf-8")

    resp = api_client.post("/api/screen")
    assert resp.status_code == 200

    resp_results = api_client.get("/api/results")
    assert resp_results.json()["receipts"]["jev_calls"] == 9

    def _fail(*args, **kwargs):
        return CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(api.subprocess, "run", _fail)
    resp_fail = api_client.post("/api/screen", params={"mode": "rescreen"})
    assert resp_fail.status_code == 500
    assert "boom" in resp_fail.json()["detail"]

    resp_still9 = api_client.get("/api/results")
    assert resp_still9.json()["receipts"]["jev_calls"] == 9

    def _ok(*args, **kwargs):
        updated12 = sample_run.model_copy(update={"receipts": sample_run.receipts.model_copy(update={"jev_calls": 12})})
        results_path.write_text(updated12.model_dump_json(), encoding="utf-8")
        return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(api.subprocess, "run", _ok)
    resp_ok = api_client.post("/api/screen", params={"mode": "rescreen"})
    assert resp_ok.status_code == 200
    assert resp_ok.json()["receipts"]["jev_calls"] == 12


def test_index_has_dashboard_elements(api_client):
    resp = api_client.get("/")
    assert resp.status_code == 200
    body = resp.text
    for needle in ['id="receipts"', 'id="dial"', 'id="dial-value"', 'id="counts"', 'id="client-table"', 'id="error"']:
        assert needle in body


def test_index_has_chips_and_wiring(api_client):
    resp = api_client.get("/")
    body = resp.text
    for needle in [
        'data-status="CLEAR"',
        'data-status="REVIEW"',
        'data-status="FLAGGED"',
        'data-status="NO_COVERAGE"',
        "/api/results?strictness=",
        "function openDrawer",
        "data-client-id",
    ]:
        assert needle in body


def test_index_no_external_assets(api_client):
    resp = api_client.get("/")
    body = resp.text
    assert "<script src=" not in body
    assert 'rel="stylesheet"' not in body


def test_index_has_drawer_elements(api_client):
    resp = api_client.get("/")
    body = resp.text
    for needle in [
        'id="drawer"',
        'id="drawer-close"',
        'id="drawer-title"',
        'id="drawer-articles"',
        'id="drawer-actions"',
        "/api/client/",
        "title only",
    ]:
        assert needle in body


def test_drawer_payload_collision(api_client):
    resp = api_client.get("/api/client/c2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "CLEAR"

    article = body["articles"][0]
    assert article["verdict"] == "CLEAR"
    assert article["reason"] == "different person"
    assert article["title_only"] is True
    for key in ("same_person", "is_subject", "risk_type", "risk_conf", "severity"):
        assert key in article["decision"]


def test_drawer_payload_error_article(api_client):
    resp = api_client.get("/api/client/c5")
    assert resp.status_code == 200
    body = resp.json()
    article = body["articles"][0]
    assert article["verdict"] == "REVIEW"
    assert article["decision"]["error"] == "timeout"
    assert article["decision"]["same_person"] is None
