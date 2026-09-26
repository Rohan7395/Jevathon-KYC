import copy
import json
import logging

import httpx

from app import jev_client
from app.jev_client import judge_article
from app.models import Article, Client

GOOD_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "same_person": {"type": "noul", "noul": 0.91},
        "is_subject": {"type": "noul", "noul": 0.88},
        "risk_type": {
            "type": "choice",
            "choice": "fraud",
            "confidence": 0.8,
            "probabilities": {"fraud": 0.9, "none": 0.02},
        },
        "severity": {
            "type": "score",
            "score": 1.0,
            "confidence": 0.8,
            "probabilities": {"0": 0.1, "1": 0.8},
        },
    },
    "usage": {"input_tokens": 392, "output_tokens": 65},
}


def make_client() -> Client:
    return Client(
        id="C001",
        name="Sam Bankman-Fried",
        birth_year=1992,
        city=None,
        country="US",
        occupation="crypto exchange founder",
        organization="FTX",
    )


def make_article() -> Article:
    return Article(
        id="art1",
        client_id="C001",
        url="https://a/1",
        title="Bankman-Fried sentenced",
        domain="a.com",
        seen_date="20250101T000000Z",
        language="English",
        text=None,
        title_only=True,
    )


async def noop_sleep(_):
    pass


async def test_success_maps_all_fields_and_sends_expected_request():
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json=GOOD_RESPONSE)

    client = make_client()
    article = make_article()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        decision = await judge_article(
            client, article, http, base_url="https://api.typesafe.ai", api_key="k", sleep=noop_sleep
        )

    assert decision.same_person == 0.91
    assert decision.is_subject == 0.88
    assert decision.risk_type == "fraud"
    assert decision.risk_conf == 0.8
    assert decision.risk_probs == {"fraud": 0.9, "none": 0.02}
    assert decision.severity == 1
    assert decision.severity_conf == 0.8
    assert decision.input_tokens == 392
    assert decision.model == "jev-1.13.0"
    assert decision.error is None

    req = captured["request"]
    assert str(req.url).endswith("/v1/systemone")
    assert req.headers["authorization"] == "Bearer k"

    body = json.loads(req.content)
    assert body["state"]["article"]["title"] == article.title
    assert set(body["questions"].keys()) == {"same_person", "is_subject", "risk_type", "severity"}


async def test_retries_once_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="too many requests")
        return httpx.Response(200, json=GOOD_RESPONSE)

    sleeps = []

    async def recording_sleep(s):
        sleeps.append(s)

    client = make_client()
    article = make_article()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        decision = await judge_article(
            client,
            article,
            http,
            base_url="https://api.typesafe.ai",
            api_key="k",
            backoff_s=1.0,
            sleep=recording_sleep,
        )

    assert calls["n"] == 2
    assert sleeps == [1.0]
    assert decision.error is None
    assert decision.same_person == 0.91


async def test_503_twice_returns_http_error_no_exception():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    client = make_client()
    article = make_article()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        decision = await judge_article(
            client, article, http, base_url="https://api.typesafe.ai", api_key="k", sleep=noop_sleep
        )

    assert calls["n"] == 2
    assert decision.error == "http_503"


async def test_timeout_returns_timeout_error_no_exception():
    def handler(request):
        raise httpx.ReadTimeout("t")

    client = make_client()
    article = make_article()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        decision = await judge_article(
            client, article, http, base_url="https://api.typesafe.ai", api_key="k", sleep=noop_sleep
        )

    assert decision.error == "timeout"


async def test_missing_field_and_first_response_logged(monkeypatch, caplog):
    payload = copy.deepcopy(GOOD_RESPONSE)
    del payload["answers"]["is_subject"]

    def handler(request):
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(jev_client, "_first_logged", False)

    client = make_client()
    article = make_article()

    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            decision = await judge_article(
                client, article, http, base_url="https://api.typesafe.ai", api_key="k", sleep=noop_sleep
            )

    assert decision.error == "missing_field:is_subject"
    assert decision.same_person == 0.91
    assert "First Jev response" in caplog.text
