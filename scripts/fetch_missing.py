import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

import httpx

from app.config import GDELT_THROTTLE_S, NEWS_CACHE_JSON, PORTFOLIO_CSV
from app.gdelt import fetch_all, load_cache, load_portfolio, save_cache

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    all_clients = load_portfolio(PORTFOLIO_CSV)

    if NEWS_CACHE_JSON.exists():
        articles_by_client, fetch_failures = load_cache(NEWS_CACHE_JSON)
    else:
        articles_by_client, fetch_failures = {}, [c.id for c in all_clients]

    missing_ids = {c.id for c in all_clients if c.id not in articles_by_client}
    retry_clients = [c for c in all_clients if c.id in missing_ids]

    if not retry_clients:
        print("Nothing missing — cache already has all clients.")
        return

    print(f"Retrying {len(retry_clients)} missing client(s): {','.join(c.id for c in retry_clients)}")

    def on_progress(i, total, client, n_articles):
        if n_articles is None:
            print(f"[{i}/{total}] {client.name}: FETCH FAILED")
        else:
            print(f"[{i}/{total}] {client.name}: {n_articles} articles")

    with httpx.Client() as http:
        new_articles_by_client, still_failing = fetch_all(
            retry_clients,
            http,
            throttle_s=GDELT_THROTTLE_S,
            on_progress=on_progress,
        )

    articles_by_client.update(new_articles_by_client)
    fetch_failures = sorted((set(fetch_failures) - set(new_articles_by_client.keys())) | set(still_failing))

    save_cache(NEWS_CACHE_JSON, articles_by_client, fetch_failures)

    total_articles = sum(len(v) for v in articles_by_client.values())
    print(
        f"Wrote data/news_cache.json ({total_articles} articles, {len(articles_by_client)} clients); "
        f"recovered {len(new_articles_by_client)}/{len(retry_clients)} this run"
    )
    if fetch_failures:
        print(f"still fetch_failures: {','.join(fetch_failures)}")


if __name__ == "__main__":
    main()
