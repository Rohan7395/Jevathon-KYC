import logging
import os

from dotenv import load_dotenv
from openai import OpenAI

from app.models import Article, ArticleDecision, Client, Verdict

MAX_MEMO_LINES: int = 6
MAX_MEMO_ARTICLES: int = 10

SEVERITY_LABELS = [
    "Rumor or unverified allegation",
    "Formal investigation, raid, or lawsuit",
    "Criminally charged or indicted",
    "Convicted, sanctioned, or penalized",
]

logger = logging.getLogger(__name__)


class MemoError(Exception):
    pass


MemoItem = tuple[Article, ArticleDecision | None, Verdict, str]


def select_memo_articles(
    articles: list[Article],
    decisions: dict[str, ArticleDecision],
    verdicts: dict[str, tuple[Verdict, str]],
    limit: int = MAX_MEMO_ARTICLES,
) -> list[MemoItem]:
    items: list[MemoItem] = []
    for a in articles:
        verdict, reason = verdicts.get(a.id, (Verdict.REVIEW, "fail-closed: no decision"))
        if verdict not in (Verdict.HIT, Verdict.REVIEW):
            continue
        items.append((a, decisions.get(a.id), verdict, reason))

    def sort_key(item: MemoItem):
        _article, decision, verdict, _reason = item
        verdict_rank = 0 if verdict == Verdict.HIT else 1
        severity = decision.severity if decision is not None and decision.severity is not None else -1
        same_person = decision.same_person if decision is not None and decision.same_person is not None else -1
        return (verdict_rank, -severity, -same_person)

    items.sort(key=sort_key)
    return items[:limit]


def _fmt(value: float | int | str | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def build_memo_prompt(client: Client, items: list[MemoItem]) -> list[dict[str, str]]:
    system = (
        "You write a compliance analyst memo of at most 6 lines.\n"
        "Use only the facts provided below; never infer or speculate beyond them.\n"
        "Cite the article URL for every claim.\n"
        "Do not assign a status, decision, or recommendation such as clear or flag.\n"
        "Write for a human analyst: summarize what each article alleges, which client identity details "
        "(occupation, organization, city, age) match or conflict, and the legal stage (allegation, "
        "investigation, charge, conviction).\n"
        "Do not recite raw field names, scores, or metadata such as title_only or seen_date.\n"
        "Write in plain text with no markdown headers."
    )

    identity_lines = []
    for label, value in (
        ("name", client.name),
        ("birth_year", client.birth_year),
        ("city", client.city),
        ("country", client.country),
        ("occupation", client.occupation),
        ("organization", client.organization),
    ):
        if value is not None:
            identity_lines.append(f"{label}: {value}")

    blocks = []
    for article, decision, verdict, reason in items:
        if decision is not None and decision.error:
            jev_line = "Jev error — not assessed"
        else:
            severity = decision.severity if decision is not None else None
            severity_text = "n/a" if severity is None else f"{severity} ({SEVERITY_LABELS[severity]})"
            jev_line = (
                f"same_person: {_fmt(decision.same_person if decision is not None else None)}, "
                f"is_subject: {_fmt(decision.is_subject if decision is not None else None)}, "
                f"risk_type: {_fmt(decision.risk_type if decision is not None else None)}, "
                f"severity: {severity_text}"
            )

        blocks.append(
            f"title: {article.title}\n"
            f"text: {article.text[:1500] if article.text else '(headline only)'}\n"
            f"url: {article.url}\n"
            f"domain: {_fmt(article.domain)}\n"
            f"seen_date: {_fmt(article.seen_date)}\n"
            f"verdict: {verdict.value}\n"
            f"reason: {reason}\n"
            f"{jev_line}\n"
            f"title_only: {article.title_only}"
        )

    user = "Client:\n" + "\n".join(identity_lines) + "\n\nArticles:\n" + "\n\n".join(blocks)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def enforce_line_limit(text: str, max_lines: int = MAX_MEMO_LINES) -> str:
    lines = [line for line in text.strip().split("\n") if line.strip()]
    return "\n".join(lines[:max_lines])


def generate_memo(
    client: Client,
    articles: list[Article],
    decisions: dict[str, ArticleDecision],
    verdicts: dict[str, tuple[Verdict, str]],
    llm: OpenAI | None = None,
    model: str | None = None,
) -> str:
    items = select_memo_articles(articles, decisions, verdicts)
    if not items:
        raise MemoError("no_hit_or_review_articles")

    if llm is None:
        import app.config  # noqa: F401 — ensures the project's .env conventions are honored

        load_dotenv()

        base_url = os.getenv("LLM_BASE_URL")
        api_key = os.getenv("LLM_API_KEY")
        llm_model = os.getenv("LLM_MODEL")

        for name, value in (("LLM_BASE_URL", base_url), ("LLM_API_KEY", api_key), ("LLM_MODEL", llm_model)):
            if not value:
                raise MemoError(f"llm_not_configured: missing {name}")

        llm = OpenAI(base_url=base_url, api_key=api_key)
        if model is None:
            model = llm_model

    messages = build_memo_prompt(client, items)

    try:
        resp = llm.chat.completions.create(
            model=model, messages=messages, temperature=0.2, max_tokens=4000, timeout=60,
        )
    except Exception as e:
        raise MemoError(f"llm_call_failed: {type(e).__name__}: {e}") from e

    content = resp.choices[0].message.content
    if content is None or not content.strip():
        raise MemoError("empty_llm_response")

    return enforce_line_limit(content)
