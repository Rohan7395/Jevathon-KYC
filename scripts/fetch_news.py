import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

import httpx

from app.config import GDELT_THROTTLE_S, NEWS_CACHE_JSON, PORTFOLIO_CSV
from app.gdelt import fetch_all, load_portfolio, save_cache

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    clients = load_portfolio(PORTFOLIO_CSV)

    def on_progress(i, total, client, n_articles):
        if n_articles is None:
            print(f"[{i}/{total}] {client.name}: FETCH FAILED")
        else:
            print(f"[{i}/{total}] {client.name}: {n_articles} articles")

    with httpx.Client() as http:
        articles_by_client, fetch_failures = fetch_all(
            clients,
            http,
            throttle_s=GDELT_THROTTLE_S,
            on_progress=on_progress,
        )

    save_cache(NEWS_CACHE_JSON, articles_by_client, fetch_failures)

    total_articles = sum(len(v) for v in articles_by_client.values())
    print(f"Wrote data/news_cache.json ({total_articles} articles, {len(articles_by_client)} clients)")

    if fetch_failures:
        print(f"fetch_failures: {','.join(fetch_failures)}")


if __name__ == "__main__":
    main()
