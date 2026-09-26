import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Article, ArticleDecision, Client, ScreenRun, Thresholds, Verdict
from app.router import verdict_for_article

KEYWORDS: list[str] = ["fraud", "arrest", "charged", "bribe", "laundering",
                       "sanction", "convicted", "probe", "raid"]

logger = logging.getLogger(__name__)


def load_labels(path: Path) -> list[tuple[str, str, int]]:
    labels: list[tuple[str, str, int]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            client_id = row["client_id"]
            article_id = row["article_id"]
            raw = row["is_hit"].strip()
            if raw not in ("0", "1"):
                logger.warning("skip label %s,%s: invalid is_hit %r", client_id, article_id, row["is_hit"])
                continue
            labels.append((client_id, article_id, int(raw)))
    return labels


def load_run(path: Path) -> ScreenRun:
    return ScreenRun.model_validate_json(path.read_text())


def last_name(client: Client) -> str:
    return client.name.split()[-1].lower()


def keyword_hit(article: Article, client: Client) -> bool:
    if last_name(client) not in article.title.lower():
        return False
    combined = (article.title + " " + (article.text or "")).lower()
    return any(keyword in combined for keyword in KEYWORDS)


def jev_hit(decision: ArticleDecision | None, t: Thresholds) -> bool:
    if decision is None:
        return False
    verdict, _reason = verdict_for_article(decision, t)
    return verdict == Verdict.HIT


def compute_metrics(predictions: list[bool], truths: list[bool]) -> dict[str, float | int]:
    if len(predictions) != len(truths):
        raise ValueError("predictions and truths must have the same length")

    tp = sum(1 for p, t in zip(predictions, truths) if p and t)
    fp = sum(1 for p, t in zip(predictions, truths) if p and not t)
    fn = sum(1 for p, t in zip(predictions, truths) if not p and t)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    return {"precision": precision, "recall": recall, "false_positives": fp, "tp": tp, "fn": fn, "n": len(truths)}


def evaluate(
    run: ScreenRun, labels: list[tuple[str, str, int]]
) -> tuple[dict[str, dict[str, float | int]], int]:
    articles_by_id = {a.id: a for a in run.articles}
    clients_by_id = {c.id: c for c in run.clients}
    decisions_by_article_id = {d.article_id: d for d in run.decisions}
    t = Thresholds()

    jev_preds: list[bool] = []
    kw_preds: list[bool] = []
    truths: list[bool] = []
    skipped = 0

    for client_id, article_id, is_hit in labels:
        article = articles_by_id.get(article_id)
        client = clients_by_id.get(client_id)

        if article is None:
            logger.warning("skip label %s,%s: unknown article_id", client_id, article_id)
            skipped += 1
            continue
        if client is None:
            logger.warning("skip label %s,%s: unknown client_id", client_id, article_id)
            skipped += 1
            continue
        if article.client_id != client_id:
            logger.warning("skip label %s,%s: article belongs to a different client", client_id, article_id)
            skipped += 1
            continue

        decision = decisions_by_article_id.get(article_id)
        truths.append(bool(is_hit))
        jev_preds.append(jev_hit(decision, t))
        kw_preds.append(keyword_hit(article, client))

    results = {
        "Jev": compute_metrics(jev_preds, truths),
        "keyword_baseline": compute_metrics(kw_preds, truths),
    }
    return results, skipped


def format_table(results: dict[str, dict[str, float | int]]) -> str:
    lines = ["method | precision | recall | false_positives"]
    for name in ("Jev", "keyword_baseline"):
        m = results[name]
        lines.append(f"{name} | {m['precision']:.2f} | {m['recall']:.2f} | {m['false_positives']}")
    return "\n".join(lines)


def main(labels_path: Path = Path("data/labels.csv"), results_path: Path = Path("data/results.json")) -> int:
    try:
        labels = load_labels(labels_path)
    except FileNotFoundError:
        print(f"error: labels file not found: {labels_path}", file=sys.stderr)
        return 1

    try:
        run = load_run(results_path)
    except FileNotFoundError:
        print(f"error: results not found: {results_path} (run scripts/run_screen.py first)", file=sys.stderr)
        return 1

    results, skipped = evaluate(run, labels)

    n = results["Jev"]["n"]
    if n == 0:
        print("error: no usable labels", file=sys.stderr)
        return 1

    print(format_table(results))
    print(f"n = {n} labeled pairs (skipped {skipped})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
