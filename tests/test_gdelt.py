import hashlib
import logging

import httpx
import pytest

from app.gdelt import (
    GdeltFetchError,
    fetch_all,
    fetch_client_articles,
    load_cache,
    load_portfolio,
    parse_articles,
    save_cache,
)
from app.models import Article, Client


def make_client(id="C001", name="Sam Bankman-Fried"):
    return Client(id=id, name=name)


def test_persistent_non_json_raises_and_retries_once(caplog):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, text="Please limit requests to one every 5 seconds...")

    sleeps = []
    client = make_client()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(GdeltFetchError):
                fetch_client_articles(client, http, sleep=sleeps.append)

    assert calls["n"] == 2
    assert sleeps == [10.0]
    assert "Please limit requests" in caplog.text


def test_fetch_all_records_failure_for_persistent_non_json():
    def handler(request):
        return httpx.Response(200, text="Please limit requests to one every 5 seconds...")

    sleeps = []
    client = make_client()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = fetch_all([client], http, sleep=sleeps.append)

    assert result == ({}, ["C001"])


def test_retries_then_succeeds_and_parses_article():
    calls = {"n": 0}
    good_payload = {
        "articles": [
            {
                "url": "https://a/1",
                "title": "Bankman-Fried trial update",
                "domain": "a",
                "seendate": "20250101T000000Z",
                "language": "English",
            }
        ]
    }

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text="not json")
        return httpx.Response(200, json=good_payload)

    sleeps = []
    client = make_client()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        articles = fetch_client_articles(client, http, sleep=sleeps.append)

    assert len(articles) == 1
    article = articles[0]
    assert article.id == hashlib.sha1(b"C001https://a/1").hexdigest()[:12]
    assert article.title_only is True
    assert article.text is None
    assert article.seen_date == "20250101T000000Z"


def test_parse_articles_filters_matches_and_dedupes():
    client = make_client()
    payload = {
        "articles": [
            {"url": "https://x/1", "title": "BANKMAN-FRIED sentenced"},
            {"url": "https://x/2", "title": "Crypto markets fall"},
            {"url": "https://x/1", "title": "BANKMAN-FRIED sentenced"},
        ]
    }

    articles = parse_articles(client, payload)

    assert len(articles) == 1
    assert articles[0].url == "https://x/1"
    assert parse_articles(client, {}) == []


def test_fetch_all_throttles_between_clients():
    payload = {"articles": []}

    def handler(request):
        return httpx.Response(200, json=payload)

    sleeps = []
    clients = [make_client(id=f"C00{i}", name=f"Name{i}") for i in range(1, 4)]

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        articles_by_client, failures = fetch_all(clients, http, sleep=sleeps.append)

    assert sleeps == [5.0, 5.0]
    assert set(articles_by_client.keys()) == {"C001", "C002", "C003"}
    assert failures == []


def test_load_portfolio_empty_birth_year(tmp_path):
    csv_path = tmp_path / "portfolio.csv"
    csv_path.write_text(
        "id,name,birth_year,city,country,occupation,organization\n"
        "C001,Test Person,,City,US,job,org\n"
    )

    clients = load_portfolio(csv_path)

    assert len(clients) == 1
    assert clients[0].birth_year is None


def test_save_and_load_cache_round_trip(tmp_path):
    cache_path = tmp_path / "news_cache.json"
    article = Article(
        id="abc123456789",
        client_id="C001",
        url="https://a/1",
        title="Title",
        domain="a",
        seen_date="20250101T000000Z",
        language="English",
        text=None,
        title_only=True,
    )
    articles_by_client = {"C001": [article]}
    fetch_failures = ["C002"]

    save_cache(cache_path, articles_by_client, fetch_failures)
    loaded_articles, loaded_failures = load_cache(cache_path)

    assert loaded_articles == articles_by_client
    assert loaded_failures == fetch_failures
