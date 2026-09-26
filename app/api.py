import asyncio
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.audit import append_decisions
from app.config import AUDIT_JSONL, load_settings
from app.gdelt import fetch_new_articles
from app.jev_client import judge_article
from app.models import (
    Article,
    ArticleDecision,
    Client,
    ClientResult,
    ScreenRun,
    Status,
    Thresholds,
    Verdict,
)
from app.router import status_for_client, thresholds_from_strictness, verdict_for_article
from app.memo import MemoError, generate_memo
from app.screening import screen_new_articles

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "data" / "results.json"
STATIC_DIR = ROOT / "static"

app = FastAPI(title="KYC Sentinel")

_RUN: ScreenRun | None = None
_memo_cache: dict[str, str] = {}
_monitor_lock = asyncio.Lock()
_jev_http: httpx.AsyncClient | None = None

logger = logging.getLogger(__name__)

_VERDICT_RANK = {Verdict.HIT: 0, Verdict.REVIEW: 1, Verdict.CLEAR: 2}


def _short_msg(exc: Exception) -> str:
    msg = str(exc).strip()
    first_line = msg.splitlines()[0] if msg else type(exc).__name__
    return first_line[:200]


def load_run(path: Path) -> ScreenRun:
    run = ScreenRun.model_validate_json(path.read_text(encoding="utf-8"))
    logger.info(
        "loaded ScreenRun: %d clients, %d articles, %d decisions",
        len(run.clients), len(run.articles), len(run.decisions),
    )
    return run


def get_run() -> ScreenRun:
    global _RUN
    if _RUN is None:
        _RUN = load_run(RESULTS_PATH)
    return _RUN


def reset_cache() -> None:
    global _RUN
    _RUN = None


def set_run(run: ScreenRun) -> None:
    global _RUN
    _RUN = run


def _persist_run(run: ScreenRun) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(run.model_dump_json(indent=2))


def _get_jev_http() -> httpx.AsyncClient:
    global _jev_http
    if _jev_http is None:
        _jev_http = httpx.AsyncClient()
    return _jev_http


async def _monitor_fetch(clients: list[Client], known_ids: set[str]) -> tuple[list[Article], list[str]]:
    async with httpx.AsyncClient(timeout=20) as http:
        return await fetch_new_articles(clients, known_ids, http)


async def _monitor_judge(client: Client, article: Article) -> ArticleDecision:
    settings = load_settings()
    return await judge_article(
        client, article, _get_jev_http(),
        base_url=settings.jev_base_url,
        api_key=settings.jev_api_key,
        model=settings.jev_model,
    )


def _run_or_503() -> ScreenRun:
    try:
        return get_run()
    except (FileNotFoundError, ValidationError) as exc:
        logger.error("failed to load results.json: %s", exc)
        raise HTTPException(
            503,
            detail=f"results.json not available — run scripts/run_screen.py ({_short_msg(exc)})",
        ) from exc


def _reload_or_503() -> ScreenRun:
    global _RUN
    try:
        run = load_run(RESULTS_PATH)
    except (FileNotFoundError, ValidationError) as exc:
        logger.error("failed to reload results.json: %s", exc)
        raise HTTPException(
            503,
            detail=f"results.json not available — run scripts/run_screen.py ({_short_msg(exc)})",
        ) from exc
    _RUN = run
    return run


def route_client(
    run: ScreenRun, client: Client, t: Thresholds
) -> tuple[ClientResult, dict[str, tuple[Verdict, str]], dict[str, ArticleDecision], list[Article]]:
    arts = [a for a in run.articles if a.client_id == client.id]
    decisions_by_article_id = {d.article_id: d for d in run.decisions}

    decisions: dict[str, ArticleDecision] = {}
    for a in arts:
        d = decisions_by_article_id.get(a.id)
        if d is None:
            d = ArticleDecision(article_id=a.id, client_id=client.id, error="missing_decision")
        decisions[a.id] = d

    verdicts = {aid: verdict_for_article(d, t) for aid, d in decisions.items()}
    result = status_for_client(client.id, verdicts, decisions, t)
    return result, verdicts, decisions, arts


def build_results(run: ScreenRun, strictness: float) -> dict[str, Any]:
    t = thresholds_from_strictness(strictness)
    s_clamped = max(0.0, min(1.0, strictness))

    counts = {"CLEAR": 0, "REVIEW": 0, "FLAGGED": 0, "NO_COVERAGE": 0}
    clients_out = []
    for client in run.clients:
        result, _verdicts, _decisions, arts = route_client(run, client, t)
        counts[result.status.value] += 1
        reason = result.reasons[0] if result.reasons else ""
        clients_out.append({
            **client.model_dump(),
            **result.model_dump(mode="json"),
            "reason": reason,
            "article_count": len(arts),
        })

    return {
        "strictness": s_clamped,
        "thresholds": t.model_dump(),
        "clients": clients_out,
        "counts": counts,
        "receipts": run.receipts.model_dump(),
    }


def build_client_detail(run: ScreenRun, client_id: str, strictness: float) -> dict[str, Any] | None:
    client = next((c for c in run.clients if c.id == client_id), None)
    if client is None:
        return None

    t = thresholds_from_strictness(strictness)
    result, verdicts, decisions, arts = route_client(run, client, t)

    articles_out = []
    for a in arts:
        verdict, reason = verdicts[a.id]
        articles_out.append({
            **a.model_dump(),
            "verdict": verdict.value,
            "reason": reason,
            "decision": decisions[a.id].model_dump(),
        })
    articles_out.sort(key=lambda entry: _VERDICT_RANK[Verdict(entry["verdict"])])

    return {
        "client": client.model_dump(),
        "result": result.model_dump(mode="json"),
        "articles": articles_out,
    }


@app.get("/api/results")
def get_results(strictness: float = Query(0.5)) -> dict[str, Any]:
    run = _run_or_503()
    return build_results(run, strictness)


@app.get("/api/client/{client_id}")
def get_client(client_id: str, strictness: float = Query(0.5)) -> dict[str, Any]:
    run = _run_or_503()
    detail = build_client_detail(run, client_id, strictness)
    if detail is None:
        raise HTTPException(404, detail="client not found")
    return detail


@app.post("/api/screen")
def post_screen(mode: Literal["reload", "rescreen"] = Query("reload")) -> dict[str, Any]:
    if mode == "rescreen":
        try:
            proc = subprocess.run(
                [sys.executable, "scripts/run_screen.py"],
                cwd=ROOT, capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error("rescreen timed out: %s", exc)
            raise HTTPException(500, detail="rescreen failed: timeout") from exc

        if proc.returncode != 0:
            stderr = proc.stderr or ""
            logger.error("rescreen failed (rc=%s): %s", proc.returncode, stderr[-500:])
            tail = stderr[-300:] if stderr else "timeout"
            raise HTTPException(500, detail=f"rescreen failed: {tail}")

    run = _reload_or_503()
    _memo_cache.clear()
    return {"ok": True, "mode": mode, "receipts": run.receipts.model_dump()}


@app.post("/api/memo/{client_id}")
def memo_endpoint(client_id: str) -> dict[str, Any]:
    if client_id in _memo_cache:
        return {"client_id": client_id, "memo": _memo_cache[client_id], "cached": True}

    run = get_run()
    client = next((c for c in run.clients if c.id == client_id), None)
    if client is None:
        raise HTTPException(404, detail="unknown client")

    articles = [a for a in run.articles if a.client_id == client_id]
    decisions = {d.article_id: d for d in run.decisions if d.client_id == client_id}
    t = Thresholds()
    verdicts = {
        a.id: (verdict_for_article(decisions[a.id], t) if a.id in decisions
               else (Verdict.REVIEW, "fail-closed: no decision"))
        for a in articles
    }
    result = status_for_client(client_id, verdicts, decisions, t)

    if result.status not in (Status.FLAGGED, Status.REVIEW):
        raise HTTPException(
            409, detail=f"memo only for FLAGGED/REVIEW clients (status: {result.status.value})"
        )

    try:
        memo = generate_memo(client, articles, decisions, verdicts)
    except MemoError as e:
        logger.warning("memo generation failed for %s: %s", client_id, e)
        raise HTTPException(502, detail=str(e)) from e

    _memo_cache[client_id] = memo
    return {"client_id": client_id, "memo": memo, "cached": False}


@app.post("/api/monitor/tick")
async def monitor_tick(strictness: float = Query(0.5), client_ids: str | None = Query(None)) -> dict[str, Any]:
    if _monitor_lock.locked():
        raise HTTPException(409, detail="monitor tick already running")

    async with _monitor_lock:
        run = get_run()

        if client_ids is None:
            selected = list(run.clients)
        else:
            wanted = {cid.strip() for cid in client_ids.split(",") if cid.strip()}
            selected = [c for c in run.clients if c.id in wanted]
            if not selected:
                raise HTTPException(400, detail="no matching clients")

        known = {a.id for a in run.articles}
        new_articles, fetch_failures = await _monitor_fetch(selected, known)

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        t = thresholds_from_strictness(strictness)

        def on_decision(d: ArticleDecision) -> None:
            v = verdict_for_article(d, t)
            append_decisions(AUDIT_JSONL, run_id, [d], {d.article_id: v}, strictness)

        merged, new_decisions, tick_receipts = await screen_new_articles(
            run, new_articles, _monitor_judge, on_decision,
        )

        set_run(merged)
        _persist_run(merged)

        articles_by_id = {a.id: a for a in new_articles}
        clients_by_id = {c.id: c for c in run.clients}

        hits: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        for d in new_decisions:
            v, reason = verdict_for_article(d, t)
            if v not in (Verdict.HIT, Verdict.REVIEW):
                continue
            article = articles_by_id.get(d.article_id)
            client = clients_by_id.get(d.client_id)
            alert = {
                "client_id": d.client_id,
                "client_name": client.name if client else d.client_id,
                "article_id": d.article_id,
                "title": article.title if article else "",
                "url": article.url if article else "",
                "verdict": v.value,
                "reason": reason,
            }
            (hits if v == Verdict.HIT else reviews).append(alert)

        return {
            "new_articles": len(new_articles),
            "alerts": hits + reviews,
            "fetch_failures": fetch_failures,
            "tick_receipts": tick_receipts.model_dump(),
            "receipts": merged.receipts.model_dump(),
        }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
