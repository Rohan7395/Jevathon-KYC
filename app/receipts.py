from app.config import (
    JEV_PRICE_PER_INPUT_TOKEN,
    LLM_EQUIV_INPUT_PRICE_PER_TOKEN,
    LLM_EQUIV_OUTPUT_PRICE_PER_TOKEN,
    LLM_EQUIV_OUTPUT_TOKENS_PER_CALL,
)
from app.models import ArticleDecision, Receipts


def build_receipts(decisions: list[ArticleDecision], elapsed_s: float) -> Receipts:
    jev_calls = len(decisions)
    jev_errors = sum(1 for d in decisions if d.error is not None)
    input_tokens = sum(d.input_tokens for d in decisions)
    jev_cost_usd = input_tokens * JEV_PRICE_PER_INPUT_TOKEN
    llm_equiv_cost_usd = (
        input_tokens * LLM_EQUIV_INPUT_PRICE_PER_TOKEN
        + jev_calls * LLM_EQUIV_OUTPUT_TOKENS_PER_CALL * LLM_EQUIV_OUTPUT_PRICE_PER_TOKEN
    )

    return Receipts(
        jev_calls=jev_calls,
        jev_errors=jev_errors,
        input_tokens=input_tokens,
        jev_cost_usd=jev_cost_usd,
        elapsed_s=elapsed_s,
        llm_equiv_cost_usd=llm_equiv_cost_usd,
    )


def format_receipts(r: Receipts) -> str:
    return (
        f"Jev calls: {r.jev_calls} | errors: {r.jev_errors} | tokens: {r.input_tokens} | "
        f"Jev $: {r.jev_cost_usd:.6f} | elapsed: {r.elapsed_s:.1f}s | LLM est $: {r.llm_equiv_cost_usd:.4f}"
    )


def assert_healthy(r: Receipts) -> None:
    assert r.jev_errors < 0.2 * max(r.jev_calls, 1), f"{r.jev_errors}/{r.jev_calls} Jev failures"
