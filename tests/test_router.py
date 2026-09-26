from app.models import ArticleDecision, Status, Thresholds, Verdict
from app.router import status_for_client, thresholds_from_strictness, verdict_for_article

T = Thresholds()


def dec(**kw) -> ArticleDecision:
    defaults = dict(
        article_id="a1",
        client_id="c1",
        same_person=0.9,
        is_subject=0.9,
        risk_type="fraud",
        risk_conf=0.9,
        severity=2,
        severity_conf=0.8,
    )
    defaults.update(kw)
    return ArticleDecision(**defaults)


def test_verdict_fail_closed_on_error():
    assert verdict_for_article(dec(error="timeout"), T) == (Verdict.REVIEW, "fail-closed: timeout")


def test_verdict_fail_closed_on_missing_field():
    assert verdict_for_article(dec(severity=None), T) == (Verdict.REVIEW, "fail-closed: missing_field:severity")


def test_verdict_different_person():
    assert verdict_for_article(dec(same_person=0.1), T) == (Verdict.CLEAR, "different person")


def test_verdict_not_subject():
    assert verdict_for_article(dec(is_subject=0.1), T) == (Verdict.CLEAR, "not the subject (victim/bystander)")


def test_verdict_no_wrongdoing():
    assert verdict_for_article(dec(risk_type="none", risk_conf=0.8), T) == (Verdict.CLEAR, "no wrongdoing")


def test_verdict_hit():
    assert verdict_for_article(dec(), T) == (Verdict.HIT, "fraud, severity 2")


def test_verdict_uncertain():
    assert verdict_for_article(dec(same_person=0.5), T) == (Verdict.REVIEW, "uncertain")


def test_verdict_boundary_same_person_clear_not_inclusive():
    verdict, reason = verdict_for_article(dec(same_person=0.25), T)
    assert reason != "different person"


def test_status_no_coverage():
    result = status_for_client("c1", {}, {}, T)
    assert result.status == Status.NO_COVERAGE
    assert result.reasons == ["no articles"]
    assert result.article_verdicts == {}
    assert result.worst_article_id is None


def test_status_flagged():
    d = dec(article_id="a1", same_person=0.9, severity=2)
    verdicts = {"a1": verdict_for_article(d, T)}
    decisions = {"a1": d}

    result = status_for_client("c1", verdicts, decisions, T)

    assert result.status == Status.FLAGGED
    assert result.worst_article_id == "a1"
    assert result.reasons[0] == "fraud, severity 2"


def test_status_hit_severity_zero_is_review_not_flagged():
    d = dec(severity=0)
    verdicts = {"a1": verdict_for_article(d, T)}
    decisions = {"a1": d}

    result = status_for_client("c1", verdicts, decisions, T)

    assert result.status == Status.REVIEW


def test_status_hit_below_flag_same_person_is_review():
    d = dec(same_person=0.8, severity=3)
    verdicts = {"a1": verdict_for_article(d, T)}
    decisions = {"a1": d}

    result = status_for_client("c1", verdicts, decisions, T)

    assert result.status == Status.REVIEW


def test_status_aggregation_all_clear():
    d1 = dec(article_id="a1", same_person=0.1)
    d2 = dec(article_id="a2", same_person=0.1)
    d3 = dec(article_id="a3", same_person=0.1)
    verdicts = {
        "a1": verdict_for_article(d1, T),
        "a2": verdict_for_article(d2, T),
        "a3": verdict_for_article(d3, T),
    }
    decisions = {"a1": d1, "a2": d2, "a3": d3}

    result = status_for_client("c1", verdicts, decisions, T)

    assert result.status == Status.CLEAR
    assert result.reasons == ["different person"]
    assert result.worst_article_id is None


def test_status_aggregation_mixed_clear_and_review():
    d1 = dec(article_id="a1", same_person=0.1)
    d2 = dec(article_id="a2", same_person=0.5)
    verdicts = {
        "a1": verdict_for_article(d1, T),
        "a2": verdict_for_article(d2, T),
    }
    decisions = {"a1": d1, "a2": d2}

    result = status_for_client("c1", verdicts, decisions, T)

    assert result.status == Status.REVIEW
    assert result.worst_article_id == "a2"
    assert result.reasons == ["uncertain", "different person"]
    assert result.article_verdicts == {"a1": Verdict.CLEAR, "a2": Verdict.REVIEW}


def test_thresholds_from_strictness_midpoint_equals_defaults():
    assert thresholds_from_strictness(0.5) == Thresholds()


def test_thresholds_from_strictness_bounds():
    t0 = thresholds_from_strictness(0)
    assert (t0.same_person_clear, t0.not_subject_clear, t0.none_clear_conf) == (0.40, 0.30, 0.55)

    t1 = thresholds_from_strictness(1)
    assert (t1.same_person_clear, t1.not_subject_clear, t1.none_clear_conf) == (0.10, 0.10, 0.90)


def test_thresholds_from_strictness_clamped():
    assert thresholds_from_strictness(-1) == thresholds_from_strictness(0)
    assert thresholds_from_strictness(2) == thresholds_from_strictness(1)


def test_thresholds_from_strictness_hit_side_fixed_across_s():
    for s in (0, 0.25, 0.5, 0.75, 1):
        t = thresholds_from_strictness(s)
        assert t.same_person_hit == 0.70
        assert t.is_subject_hit == 0.60
        assert t.hit_risk_conf == 0.70
        assert t.flag_same_person == 0.85


def test_monotonic_dial_clear_non_increasing_hit_constant():
    decisions = [
        dec(article_id="always_clear_low_same_person", same_person=0.05),
        dec(article_id="clear_then_review_same_person", same_person=0.35),
        dec(article_id="clear_then_review_is_subject", is_subject=0.15),
        dec(article_id="clear_then_review_none", risk_type="none", risk_conf=0.6),
        dec(article_id="always_hit_1"),
        dec(article_id="always_hit_2", same_person=0.95, is_subject=0.95, severity=3),
        dec(article_id="always_review_error", error="network:ConnectError"),
        dec(article_id="always_review_missing", same_person=None),
        dec(article_id="always_review_uncertain", same_person=0.5, is_subject=0.5, risk_conf=0.5),
        dec(article_id="always_clear_high_conf_none", risk_type="none", risk_conf=0.95),
    ]

    s_values = [round(i * 0.1, 1) for i in range(11)]
    clear_counts = []
    hit_counts = []

    for s in s_values:
        t = thresholds_from_strictness(s)
        verdicts = [verdict_for_article(d, t)[0] for d in decisions]
        clear_counts.append(verdicts.count(Verdict.CLEAR))
        hit_counts.append(verdicts.count(Verdict.HIT))

    assert all(clear_counts[i] >= clear_counts[i + 1] for i in range(len(clear_counts) - 1))
    assert len(set(hit_counts)) == 1


def test_monotonic_dial_none_example_clear_then_review():
    d = dec(risk_type="none", risk_conf=0.6)

    verdict_at_0, _ = verdict_for_article(d, thresholds_from_strictness(0.0))
    verdict_at_half, _ = verdict_for_article(d, thresholds_from_strictness(0.5))

    assert verdict_at_0 == Verdict.CLEAR
    assert verdict_at_half == Verdict.REVIEW
