import json
from datetime import datetime, timezone
from pathlib import Path

from app.models import ArticleDecision, Verdict


def append_decisions(
    path: Path,
    run_id: str,
    decisions: list[ArticleDecision],
    verdicts: dict[str, tuple[Verdict, str]],
    strictness: float,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()

    lines = []
    for d in decisions:
        verdict, reason = verdicts.get(d.article_id, (Verdict.REVIEW, "fail-closed: no verdict"))
        lines.append(
            json.dumps(
                {
                    "ts": ts,
                    "run_id": run_id,
                    "client_id": d.client_id,
                    "article_id": d.article_id,
                    "verdict": verdict.value,
                    "reason": reason,
                    "strictness": strictness,
                    "decision": d.model_dump(),
                }
            )
        )

    with open(path, "a") as f:
        for line in lines:
            f.write(line + "\n")

    return len(lines)
