import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app.models import Article, ArticleDecision, Client

logger = logging.getLogger(__name__)

_first_logged = False

QUESTIONS = {
    "same_person": {
        "type": "noul",
        "instructions": "The article is about the same individual described in state.client (not just someone with the same name).",
    },
    "is_subject": {
        "type": "noul",
        "instructions": "In the article, this individual is accused of, investigated for, charged with, or convicted of wrongdoing (not a victim, witness, commentator, or bystander).",
    },
    "risk_type": {
        "type": "choice",
        "instructions": "The main type of wrongdoing the article attributes to this individual.",
        "criteria": {
            "fraud": "Financial fraud, scams, embezzlement, tax evasion",
            "corruption_bribery": "Bribery, kickbacks, political corruption",
            "money_laundering": "Money laundering or hiding illicit funds",
            "sanctions_terror": "Sanctions violations or terrorism financing",
            "violent_crime": "Violent or organized crime",
            "regulatory_action": "Fines, bans, or enforcement by a regulator",
            "none": "No wrongdoing attributed to this individual",
        },
    },
    "severity": {
        "type": "score",
        "instructions": "How far the legal process has gone for this individual.",
        "criteria": [
            "Rumor or unverified allegation",
            "Formal investigation, raid, or lawsuit",
            "Criminally charged or indicted",
            "Convicted, sanctioned, or penalized",
        ],
    },
}

_REQUIRED_FIELDS = ["same_person", "is_subject", "risk_type", "risk_conf", "severity", "severity_conf"]


def build_request_body(client: Client, article: Article, model: str) -> dict:
    return {
        "model": model,
        "state": {
            "client": {
                "name": client.name,
                "birth_year": client.birth_year,
                "city": client.city,
                "country": client.country,
                "occupation": client.occupation,
                "organization": client.organization,
            },
            "article": {
                "title": article.title,
                "text": article.text[:1500] if article.text is not None else None,
                "date": article.seen_date,
                "source": article.domain,
            },
        },
        "questions": QUESTIONS,
    }


def _noul(answers: dict, key: str):
    obj = answers.get(key)
    return obj.get("noul") if isinstance(obj, dict) else None


def parse_response(article: Article, payload: dict) -> ArticleDecision:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        answers = {}

    same_person = _noul(answers, "same_person")
    is_subject = _noul(answers, "is_subject")

    risk_type_obj = answers.get("risk_type")
    risk_type_obj = risk_type_obj if isinstance(risk_type_obj, dict) else {}
    risk_type = risk_type_obj.get("choice")
    risk_conf = risk_type_obj.get("confidence")
    risk_probs = risk_type_obj.get("probabilities")

    severity_obj = answers.get("severity")
    severity_obj = severity_obj if isinstance(severity_obj, dict) else {}
    severity_raw = severity_obj.get("score")
    severity_conf = severity_obj.get("confidence")
    severity = int(round(severity_raw)) if severity_raw is not None else None

    if risk_probs is None:
        logger.warning("Jev response missing risk_type.probabilities; defaulting to {}")
        risk_probs = {}

    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        logger.warning("Jev response missing usage.input_tokens; defaulting to 0")
        input_tokens = 0

    model = payload.get("model")
    if model is None:
        logger.warning("Jev response missing model; defaulting to None")

    values = {
        "same_person": same_person,
        "is_subject": is_subject,
        "risk_type": risk_type,
        "risk_conf": risk_conf,
        "severity": severity,
        "severity_conf": severity_conf,
    }

    error = None
    for name in _REQUIRED_FIELDS:
        if values[name] is None:
            error = f"missing_field:{name}"
            break

    return ArticleDecision(
        article_id=article.id,
        client_id=article.client_id,
        same_person=same_person,
        is_subject=is_subject,
        risk_type=risk_type,
        risk_conf=risk_conf,
        risk_probs=risk_probs,
        severity=severity,
        severity_conf=severity_conf,
        error=error,
        input_tokens=input_tokens,
        model=model,
    )


def _log_first_response(r: httpx.Response) -> None:
    global _first_logged
    if not _first_logged:
        logger.info("First Jev response (HTTP %s): %s", r.status_code, r.text)
        _first_logged = True


async def judge_article(
    client: Client,
    article: Article,
    http: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    model: str = "jev-latest",
    timeout_s: float = 10.0,
    backoff_s: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ArticleDecision:
    url = f"{base_url.rstrip('/')}/v1/systemone"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = build_request_body(client, article, model)

    start = time.monotonic()

    def error_decision(error: str) -> ArticleDecision:
        return ArticleDecision(
            article_id=article.id,
            client_id=article.client_id,
            error=error,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    async def post_once() -> httpx.Response:
        return await http.post(url, headers=headers, json=body, timeout=timeout_s)

    try:
        try:
            r = await post_once()
        except httpx.TimeoutException:
            return error_decision("timeout")
        except httpx.HTTPError as e:
            return error_decision(f"network:{type(e).__name__}")

        _log_first_response(r)

        if r.status_code == 429 or 500 <= r.status_code < 600:
            await sleep(backoff_s)
            try:
                r = await post_once()
            except httpx.TimeoutException:
                return error_decision("timeout")
            except httpx.HTTPError as e:
                return error_decision(f"network:{type(e).__name__}")
            _log_first_response(r)
            if r.status_code != 200:
                return error_decision(f"http_{r.status_code}")
        elif r.status_code != 200:
            return error_decision(f"http_{r.status_code}")

        try:
            payload = r.json()
        except ValueError:
            return error_decision("bad_json")

        if not isinstance(payload, dict):
            return error_decision("bad_json")

        decision = parse_response(article, payload)
        decision.latency_ms = int((time.monotonic() - start) * 1000)
        return decision
    except Exception as e:
        return error_decision(f"exception:{type(e).__name__}")
