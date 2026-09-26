import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Article, ArticleDecision, Client, Receipts, ScreenRun, Thresholds
from scripts.eval import compute_metrics, evaluate, jev_hit, keyword_hit, main


def test_compute_metrics():
    m = compute_metrics([True, True, True, False, False], [True, True, False, True, False])
    assert m["precision"] == pytest.approx(0.667, abs=0.001)
    assert m["recall"] == pytest.approx(0.667, abs=0.001)
    assert m["false_positives"] == 1
    assert m["tp"] == 2
    assert m["fn"] == 1
    assert m["n"] == 5

    m2 = compute_metrics([False, False], [False, False])
    assert m2["precision"] == 0.0
    assert m2["recall"] == 0.0


def _decision(**kw) -> ArticleDecision:
    defaults = dict(
        article_id="a1", client_id="c1",
        same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2,
    )
    defaults.update(kw)
    return ArticleDecision(**defaults)


def test_jev_hit():
    assert jev_hit(_decision(), Thresholds()) is True
    assert jev_hit(_decision(error="timeout"), Thresholds()) is False
    assert jev_hit(None, Thresholds()) is False


def _client(**kw) -> Client:
    defaults = dict(id="c1", name="Michael Jordan")
    defaults.update(kw)
    return Client(**defaults)


def _article(**kw) -> Article:
    defaults = dict(id="a1", client_id="c1", url="https://ex.com/a1", title="Title", title_only=False)
    defaults.update(kw)
    return Article(**defaults)


def test_keyword_hit():
    client = _client()
    assert keyword_hit(_article(title="Jordan charged in fraud scheme"), client) is True
    assert keyword_hit(_article(title="Jordan wins award", text=None), client) is False
    assert keyword_hit(_article(title="Smith charged"), client) is False


def test_evaluate():
    client = _client(id="c1", name="Michael Jordan")
    article = _article(id="a1", client_id="c1", title="Michael Jordan charged with fraud", title_only=False)
    decision = _decision(article_id="a1", client_id="c1", same_person=0.05, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2)
    run = ScreenRun(clients=[client], articles=[article], decisions=[decision], receipts=Receipts())

    labels = [("c1", "a1", 0), ("c1", "unknown_article", 1)]
    results, skipped = evaluate(run, labels)

    assert results["Jev"]["false_positives"] == 0
    assert results["keyword_baseline"]["false_positives"] == 1
    assert skipped == 1


def test_main(tmp_path, capsys):
    client = _client(id="c1", name="Alice Smith")
    article = _article(id="a1", client_id="c1", title="Smith charged with fraud", title_only=False)
    decision = _decision(article_id="a1", client_id="c1", same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2)
    run = ScreenRun(clients=[client], articles=[article], decisions=[decision], receipts=Receipts())

    results_path = tmp_path / "results.json"
    results_path.write_text(run.model_dump_json(), encoding="utf-8")

    labels_path = tmp_path / "labels.csv"
    labels_path.write_text("client_id,article_id,is_hit\nc1,a1,1\n", encoding="utf-8")

    code = main(labels_path, results_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "method | precision | recall | false_positives" in out
    assert "Jev |" in out
    assert "keyword_baseline |" in out

    missing_code = main(tmp_path / "missing.csv", results_path)
    assert missing_code == 1
