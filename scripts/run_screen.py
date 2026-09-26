import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app.audit import append_decisions
from app.config import (
    AUDIT_JSONL,
    DEFAULT_STRICTNESS,
    JEV_CONCURRENCY,
    NEWS_CACHE_JSON,
    PORTFOLIO_CSV,
    RESULTS_JSON,
    load_settings,
)
from app.gdelt import load_cache, load_portfolio
from app.jev_client import judge_article
from app.receipts import assert_healthy, format_receipts
from app.router import thresholds_from_strictness
from app.screening import route_run, save_run, screen, verdicts_for_run

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main() -> None:
    settings = load_settings()
    clients = load_portfolio(PORTFOLIO_CSV)
    articles_by_client, fetch_failures = load_cache(NEWS_CACHE_JSON)

    if fetch_failures:
        print(f"WARNING fetch_failures (shown as NO_COVERAGE): {','.join(fetch_failures)}")

    async with httpx.AsyncClient() as http:
        judge = lambda c, a: judge_article(
            c, a, http,
            base_url=settings.jev_base_url,
            api_key=settings.jev_api_key,
            model=settings.jev_model,
        )

        run = await screen(clients, articles_by_client, judge, concurrency=JEV_CONCURRENCY)

    t = thresholds_from_strictness(DEFAULT_STRICTNESS)
    verdicts = verdicts_for_run(run, t)
    results = route_run(run, t)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    append_decisions(AUDIT_JSONL, run_id, run.decisions, verdicts, DEFAULT_STRICTNESS)

    print("client | status | reason")
    for client, result in zip(run.clients, results):
        reason = result.reasons[0] if result.reasons else ""
        print(f"{client.name:<22} | {result.status.value:<11} | {reason}")

    print(format_receipts(run.receipts))

    assert_healthy(run.receipts)

    save_run(RESULTS_JSON, run)
    print("Wrote data/results.json")


if __name__ == "__main__":
    asyncio.run(main())
