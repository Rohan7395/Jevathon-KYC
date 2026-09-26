import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.config import JEV_PRICE_PER_INPUT_TOKEN
from app.models import Article, ArticleDecision, Client, ClientResult, Receipts, ScreenRun, Thresholds, Verdict
from app.receipts import build_receipts
from app.router import status_for_client, verdict_for_article

logger = logging.getLogger(__name__)

JudgeFn = Callable[[Client, Article], Awaitable[ArticleDecision]]


async def screen(
    clients: list[Client],
    articles_by_client: dict[str, list[Article]],
    judge: JudgeFn,
    *,
    concurrency: int = 8,
) -> ScreenRun:
    articles: list[Article] = []
    for client in clients:
        articles.extend(articles_by_client.get(client.id, []))

    clients_by_id = {c.id: c for c in clients}
    semaphore = asyncio.Semaphore(concurrency)

    async def judge_one(article: Article) -> ArticleDecision:
        async with semaphore:
            client = clients_by_id[article.client_id]
            try:
                return await judge(client, article)
            except Exception as e:
                return ArticleDecision(
                    article_id=article.id,
                    client_id=article.client_id,
                    error=f"exception:{type(e).__name__}",
                )

    start = time.perf_counter()
    decisions = list(await asyncio.gather(*(judge_one(a) for a in articles)))
    elapsed_s = time.perf_counter() - start

    receipts = build_receipts(decisions, elapsed_s)

    return ScreenRun(clients=clients, articles=articles, decisions=decisions, receipts=receipts)


def _effective_decision(article: Article, decisions_by_article: dict[str, ArticleDecision]) -> ArticleDecision:
    decision = decisions_by_article.get(article.id)
    if decision is None:
        return ArticleDecision(article_id=article.id, client_id=article.client_id, error="no_decision")
    return decision


def verdicts_for_run(run: ScreenRun, t: Thresholds) -> dict[str, tuple[Verdict, str]]:
    decisions_by_article = {d.article_id: d for d in run.decisions}
    return {
        article.id: verdict_for_article(_effective_decision(article, decisions_by_article), t)
        for article in run.articles
    }


def route_run(run: ScreenRun, t: Thresholds) -> list[ClientResult]:
    decisions_by_article = {d.article_id: d for d in run.decisions}
    verdicts = verdicts_for_run(run, t)

    articles_by_client: dict[str, list[Article]] = {}
    for article in run.articles:
        articles_by_client.setdefault(article.client_id, []).append(article)

    results = []
    for client in run.clients:
        client_articles = articles_by_client.get(client.id, [])
        client_verdicts = {a.id: verdicts[a.id] for a in client_articles}
        client_decisions = {a.id: _effective_decision(a, decisions_by_article) for a in client_articles}
        results.append(status_for_client(client.id, client_verdicts, client_decisions, t))
    return results


def save_run(path: Path, run: ScreenRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2))


def load_run(path: Path) -> ScreenRun:
    return ScreenRun.model_validate_json(path.read_text())


def add_receipts(a: Receipts, b: Receipts) -> Receipts:
    return Receipts(
        jev_calls=a.jev_calls + b.jev_calls,
        jev_errors=a.jev_errors + b.jev_errors,
        input_tokens=a.input_tokens + b.input_tokens,
        jev_cost_usd=a.jev_cost_usd + b.jev_cost_usd,
        elapsed_s=a.elapsed_s + b.elapsed_s,
        llm_equiv_cost_usd=a.llm_equiv_cost_usd + b.llm_equiv_cost_usd,
    )


async def screen_new_articles(
    run: ScreenRun,
    new_articles: list[Article],
    judge: JudgeFn,
    on_decision: Callable[[ArticleDecision], None],
    concurrency: int = 8,
) -> tuple[ScreenRun, list[ArticleDecision], Receipts]:
    existing_ids = {a.id for a in run.articles}
    to_screen = [a for a in new_articles if a.id not in existing_ids]

    if not to_screen:
        return run, [], Receipts()

    clients_by_id = {c.id: c for c in run.clients}
    semaphore = asyncio.Semaphore(concurrency)

    async def judge_one(article: Article) -> ArticleDecision:
        async with semaphore:
            client = clients_by_id[article.client_id]
            try:
                return await judge(client, article)
            except Exception as e:
                logger.exception("judge failed for article %s", article.id)
                return ArticleDecision(
                    article_id=article.id,
                    client_id=article.client_id,
                    error=f"exception:{type(e).__name__}",
                )

    start = time.perf_counter()
    new_decisions = list(await asyncio.gather(*(judge_one(a) for a in to_screen)))
    elapsed_s = time.perf_counter() - start

    for d in new_decisions:
        on_decision(d)

    jev_calls = len(new_decisions)
    jev_errors = sum(1 for d in new_decisions if d.error is not None)
    input_tokens = sum(d.input_tokens for d in new_decisions)
    jev_cost_usd = input_tokens * JEV_PRICE_PER_INPUT_TOKEN
    if run.receipts.input_tokens > 0:
        llm_equiv_cost_usd = run.receipts.llm_equiv_cost_usd * (input_tokens / run.receipts.input_tokens)
    else:
        llm_equiv_cost_usd = 0.0

    tick_receipts = Receipts(
        jev_calls=jev_calls,
        jev_errors=jev_errors,
        input_tokens=input_tokens,
        jev_cost_usd=jev_cost_usd,
        elapsed_s=elapsed_s,
        llm_equiv_cost_usd=llm_equiv_cost_usd,
    )

    merged_run = run.model_copy(update={
        "articles": run.articles + to_screen,
        "decisions": run.decisions + new_decisions,
        "receipts": add_receipts(run.receipts, tick_receipts),
    })

    return merged_run, new_decisions, tick_receipts
