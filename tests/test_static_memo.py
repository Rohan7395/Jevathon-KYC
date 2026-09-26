from fastapi.testclient import TestClient

from app.api import app


def test_index_has_memo_elements():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="memo-btn"' in body
    assert 'id="memo-output"' in body
    assert "/api/memo/" in body


def test_index_has_memo_js_and_link_safety():
    resp = TestClient(app).get("/")
    body = resp.text
    assert "function escapeHtml" in body
    assert "function linkify" in body
    assert 'rel="noopener' in body
