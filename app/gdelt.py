import asyncio
import csv
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from app.config import (
    GDELT_HTTP_TIMEOUT_S,
    GDELT_MAXRECORDS,
    GDELT_NONJSON_RETRY_WAIT_S,
    GDELT_THROTTLE_S,
    GDELT_URL,
)
from app.models import Article, Client

logger = logging.getLogger(__name__)


class GdeltFetchError(Exception):
    pass


def load_portfolio(path: Path) -> list[Client]:
    clients = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            birth_year = row.get("birth_year") or None
            clients.append(
                Client(
                    id=row["id"],
                    name=row["name"],
                    birth_year=int(birth_year) if birth_year else None,
                    city=row.get("city") or None,
                    country=row.get("country") or None,
                    occupation=row.get("occupation") or None,
                    organization=row.get("organization") or None,
                )
            )
    return clients


def make_article_id(client_id: str, url: str) -> str:
    return hashlib.sha1((client_id + url).encode("utf-8")).hexdigest()[:12]


def article_id_for(client_id: str, url: str) -> str:
    return make_article_id(client_id, url)


def last_name(name: str) -> str:
    return name.split()[-1]


def mentions_last_name(client: Client, title: str, text: str | None) -> bool:
    last = last_name(client.name)
    pattern = rf"\b{re.escape(last)}\b"
    if re.search(pattern, title, re.I):
        return True
    if text is not None and re.search(pattern, text, re.I):
        return True
    return False


def parse_articles(client: Client, payload: dict) -> list[Article]:
    articles: list[Article] = []
    seen_urls: set[str] = set()
    for item in payload.get("articles", []):
        url = item.get("url")
        title = item.get("title")
        if not url or not title:
            continue
        if not mentions_last_name(client, title, None):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        articles.append(
            Article(
                id=make_article_id(client.id, url),
                client_id=client.id,
                url=url,
                title=title,
                domain=item.get("domain"),
                seen_date=item.get("seendate"),
                language=item.get("language"),
                text=None,
                title_only=True,
            )
        )
    return articles


def _try_fetch(client: Client, http: httpx.Client, params: dict) -> dict | None:
    try:
        r = http.get(GDELT_URL, params=params, timeout=GDELT_HTTP_TIMEOUT_S)
    except httpx.HTTPError as e:
        logger.warning("GDELT non-JSON for %s: %r", client.name, e)
        return None

    if r.status_code != 200 or not r.text.lstrip().startswith("{"):
        logger.warning("GDELT non-JSON for %s: %r", client.name, r.text[:120])
        return None

    try:
        return json.loads(r.text)
    except json.JSONDecodeError:
        logger.warning("GDELT non-JSON for %s: %r", client.name, r.text[:120])
        return None


def fetch_client_articles(
    client: Client,
    http: httpx.Client,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Article]:
    params = {
        "query": f'"{client.name}"',
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(GDELT_MAXRECORDS),
        "sort": "datedesc",
    }

    payload = _try_fetch(client, http, params)
    if payload is None:
        sleep(GDELT_NONJSON_RETRY_WAIT_S)
        payload = _try_fetch(client, http, params)

    if payload is None:
        raise GdeltFetchError(client.id)

    return parse_articles(client, payload)


def fetch_all(
    clients: list[Client],
    http: httpx.Client,
    *,
    throttle_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[int, int, Client, int | None], None] | None = None,
) -> tuple[dict[str, list[Article]], list[str]]:
    articles_by_client: dict[str, list[Article]] = {}
    fetch_failures: list[str] = []
    total = len(clients)

    for i, client in enumerate(clients, start=1):
        if i > 1:
            sleep(throttle_s)

        try:
            articles = fetch_client_articles(client, http, sleep=sleep)
        except GdeltFetchError:
            fetch_failures.append(client.id)
            if on_progress is not None:
                on_progress(i, total, client, None)
            continue

        articles_by_client[client.id] = articles
        if on_progress is not None:
            on_progress(i, total, client, len(articles))

    return articles_by_client, fetch_failures


def save_cache(
    path: Path,
    articles_by_client: dict[str, list[Article]],
    fetch_failures: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "articles_by_client": {
            client_id: [a.model_dump() for a in articles]
            for client_id, articles in articles_by_client.items()
        },
        "fetch_failures": fetch_failures,
    }
    path.write_text(json.dumps(data, indent=2))


def load_cache(path: Path) -> tuple[dict[str, list[Article]], list[str]]:
    data = json.loads(path.read_text())
    articles_by_client = {
        client_id: [Article.model_validate(a) for a in articles]
        for client_id, articles in data["articles_by_client"].items()
    }
    fetch_failures = data["fetch_failures"]
    return articles_by_client, fetch_failures


async def _try_fetch_async(client: Client, http: httpx.AsyncClient, params: dict) -> dict | None:
    try:
        r = await http.get(GDELT_URL, params=params, timeout=GDELT_HTTP_TIMEOUT_S)
    except httpx.HTTPError as e:
        logger.warning("GDELT non-JSON for %s: %r", client.id, e)
        return None

    if r.status_code != 200 or not r.text.lstrip().startswith("{"):
        logger.warning("GDELT non-JSON for %s: %r", client.id, r.text[:120])
        return None

    try:
        return json.loads(r.text)
    except json.JSONDecodeError:
        logger.warning("GDELT non-JSON for %s: %r", client.id, r.text[:120])
        return None


async def fetch_new_articles(
    clients: list[Client],
    known_article_ids: set[str],
    http: httpx.AsyncClient,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[list[Article], list[str]]:
    new_articles: list[Article] = []
    fetch_failures: list[str] = []
    produced_ids: set[str] = set()

    for i, client in enumerate(clients):
        if i > 0:
            await sleep(GDELT_THROTTLE_S)

        params = {
            "query": f'"{client.name}"',
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(GDELT_MAXRECORDS),
            "sort": "datedesc",
        }

        payload = await _try_fetch_async(client, http, params)
        if payload is None:
            await sleep(GDELT_NONJSON_RETRY_WAIT_S)
            payload = await _try_fetch_async(client, http, params)

        if payload is None:
            fetch_failures.append(client.id)
            continue

        last = client.name.split()[-1].lower()
        for item in payload.get("articles", []):
            url = item.get("url")
            title = item.get("title")
            if not url or not title:
                continue
            if last not in title.lower():
                continue

            aid = article_id_for(client.id, url)
            if aid in known_article_ids or aid in produced_ids:
                continue
            produced_ids.add(aid)

            new_articles.append(
                Article(
                    id=aid,
                    client_id=client.id,
                    url=url,
                    title=title,
                    domain=item.get("domain"),
                    seen_date=item.get("seendate"),
                    language=item.get("language"),
                    text=None,
                    title_only=True,
                )
            )

    return new_articles, fetch_failures
