import json

import pytest

from app.audit import append_decisions
from app.models import ArticleDecision, Receipts, Verdict
from app.receipts import assert_healthy, build_receipts, format_receipts


def make_decision(article_id="a1", client_id="c1", **kw) -> ArticleDecision:
    defaults = dict(article_id=article_id, client_id=client_id)
    defaults.update(kw)
    return ArticleDecision(**defaults)


def test_append_decisions_writes_expected_lines(tmp_path):
    path = tmp_path / "a.jsonl"
    d1 = make_decision(article_id="a1", client_id="c1")
    d2 = make_decision(article_id="a2", client_id="c1")
    verdicts = {d1.article_id: (Verdict.HIT, "fraud, severity 2")}

    n1 = append_decisions(path, "run1", [d1, d2], verdicts, 0.5)
    n2 = append_decisions(path, "run1", [d1, d2], verdicts, 0.5)

    assert n1 == 2
    assert n2 == 2

    lines = path.read_text().splitlines()
    assert len(lines) == 4

    expected_keys = {"ts", "run_id", "client_id", "article_id", "verdict", "reason", "strictness", "decision"}
    parsed = [json.loads(line) for line in lines]
    for obj in parsed:
        assert set(obj.keys()) == expected_keys

    d2_lines = [obj for obj in parsed if obj["article_id"] == "a2"]
    assert len(d2_lines) == 2
    for obj in d2_lines:
        assert obj["verdict"] == "REVIEW"
        assert obj["reason"] == "fail-closed: no verdict"

    d1_lines = [obj for obj in parsed if obj["article_id"] == "a1"]
    assert len(d1_lines) == 2
    for obj in d1_lines:
        assert obj["verdict"] == "HIT"
        assert obj["reason"] == "fraud, severity 2"


def test_build_receipts():
    decisions = [
        make_decision(article_id="a1", input_tokens=1000),
        make_decision(article_id="a2", input_tokens=500, error="timeout"),
    ]

    r = build_receipts(decisions, 3.21)

    assert r.jev_calls == 2
    assert r.jev_errors == 1
    assert r.input_tokens == 1500
    assert r.jev_cost_usd == pytest.approx(6.3e-5)
    assert r.llm_equiv_cost_usd == pytest.approx(0.009)


def test_format_receipts():
    decisions = [
        make_decision(article_id="a1", input_tokens=1000),
        make_decision(article_id="a2", input_tokens=500, error="timeout"),
    ]
    r = build_receipts(decisions, 3.21)

    assert format_receipts(r) == (
        "Jev calls: 2 | errors: 1 | tokens: 1500 | Jev $: 0.000063 | elapsed: 3.2s | LLM est $: 0.0090"
    )


def test_assert_healthy_raises_with_message():
    with pytest.raises(AssertionError, match=r"2/10 Jev failures"):
        assert_healthy(Receipts(jev_calls=10, jev_errors=2))


def test_assert_healthy_passes_below_threshold():
    assert_healthy(Receipts(jev_calls=10, jev_errors=1))


def test_assert_healthy_passes_zero_zero():
    assert_healthy(Receipts())
