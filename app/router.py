from app.models import ArticleDecision, ClientResult, Status, Thresholds, Verdict

_REQUIRED_FIELDS = ["same_person", "is_subject", "risk_type", "risk_conf", "severity"]


def verdict_for_article(d: ArticleDecision, t: Thresholds) -> tuple[Verdict, str]:
    if d.error:
        return Verdict.REVIEW, f"fail-closed: {d.error}"

    for name in _REQUIRED_FIELDS:
        if getattr(d, name) is None:
            return Verdict.REVIEW, f"fail-closed: missing_field:{name}"

    if d.same_person < t.same_person_clear:
        return Verdict.CLEAR, "different person"

    if d.same_person >= t.same_person_hit and d.is_subject < t.not_subject_clear:
        return Verdict.CLEAR, "not the subject (victim/bystander)"

    if d.risk_type == "none" and d.risk_conf >= t.none_clear_conf:
        return Verdict.CLEAR, "no wrongdoing"

    if (
        d.same_person >= t.same_person_hit
        and d.is_subject >= t.is_subject_hit
        and d.risk_type != "none"
        and d.risk_conf >= t.hit_risk_conf
    ):
        return Verdict.HIT, f"{d.risk_type}, severity {d.severity}"

    return Verdict.REVIEW, "uncertain"


def status_for_client(
    client_id: str,
    verdicts: dict[str, tuple[Verdict, str]],
    decisions: dict[str, ArticleDecision],
    t: Thresholds,
) -> ClientResult:
    if not verdicts:
        return ClientResult(
            client_id=client_id,
            status=Status.NO_COVERAGE,
            reasons=["no articles"],
            article_verdicts={},
            worst_article_id=None,
        )

    hit_ids = [aid for aid, (v, _) in verdicts.items() if v == Verdict.HIT]
    review_ids = [aid for aid, (v, _) in verdicts.items() if v == Verdict.REVIEW]

    flagged = any(
        decisions[aid].same_person >= t.flag_same_person and decisions[aid].severity >= 1
        for aid in hit_ids
    )

    if flagged:
        status = Status.FLAGGED
    elif hit_ids or review_ids:
        status = Status.REVIEW
    else:
        status = Status.CLEAR

    worst_article_id: str | None = None
    if hit_ids:
        worst_article_id = max(
            hit_ids,
            key=lambda aid: (decisions[aid].severity, decisions[aid].same_person),
        )
    elif review_ids:
        worst_article_id = review_ids[0]

    reasons: list[str] = []
    if worst_article_id is not None:
        reasons.append(verdicts[worst_article_id][1])

    for verdict_type in (Verdict.HIT, Verdict.REVIEW, Verdict.CLEAR):
        for aid, (v, reason) in verdicts.items():
            if v == verdict_type and reason not in reasons:
                reasons.append(reason)

    article_verdicts = {aid: v for aid, (v, _) in verdicts.items()}

    return ClientResult(
        client_id=client_id,
        status=status,
        reasons=reasons,
        article_verdicts=article_verdicts,
        worst_article_id=worst_article_id,
    )


def thresholds_from_strictness(s: float) -> Thresholds:
    s = max(0.0, min(1.0, s))

    def lerp(lo: float, hi: float) -> float:
        return round(lo + (hi - lo) * s, 6)

    return Thresholds(
        same_person_clear=lerp(0.40, 0.10),
        not_subject_clear=lerp(0.30, 0.10),
        none_clear_conf=lerp(0.55, 0.90),
    )
