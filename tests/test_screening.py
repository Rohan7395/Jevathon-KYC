import asyncio

from app.models import Article, ArticleDecision, Client, Receipts, ScreenRun, Status, Thresholds, Verdict
from app.screening import load_run, route_run, save_run, screen, verdicts_for_run


def make_client(client_id: str, name: str = "Name") -> Client:
    return Client(id=client_id, name=name)


def make_article(article_id: str, client_id: str) -> Article:
    return Article(
        id=article_id,
        client_id=client_id,
        url=f"https://x/{article_id}",
        title="title",
        text=None,
        title_only=True,
    )


async def test_screen_and_route_basic():
    c1 = make_client("C1")
    c2 = make_client("C2")
    c3 = make_client("C3")
    clients = [c1, c2, c3]

    a1 = make_article("a1", "C1")
    a2 = make_article("a2", "C1")
    a3 = make_article("a3", "C2")

    articles_by_client = {"C1": [a1, a2], "C2": [a3]}

    async def fake_judge(client, article):
        if client.id == "C1":
            same_person = 0.9
        else:
            same_person = 0.1
        return ArticleDecision(
            article_id=article.id,
            client_id=client.id,
            same_person=same_person,
            is_subject=0.9,
            risk_type="fraud",
            risk_conf=0.9,
            severity=2,
            severity_conf=0.8,
            input_tokens=100,
        )

    run = await screen(clients, articles_by_client, fake_judge)

    assert len(run.clients) == 3
    assert len(run.decisions) == 3
    assert run.receipts.jev_calls == 3

    results = route_run(run, Thresholds())
    assert [r.status for r in results] == [Status.FLAGGED, Status.CLEAR, Status.NO_COVERAGE]


async def test_screen_respects_concurrency_limit():
    clients = [make_client("C1")]
    articles = [make_article(f"a{i}", "C1") for i in range(20)]
    articles_by_client = {"C1": articles}

    in_flight = 0
    max_in_flight = 0

    async def fake_judge(client, article):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return ArticleDecision(article_id=article.id, client_id=client.id)

    await screen(clients, articles_by_client, fake_judge, concurrency=8)

    assert max_in_flight <= 8
    assert max_in_flight > 1


async def test_screen_catches_judge_exception():
    clients = [make_client("C1")]
    a1 = make_article("a1", "C1")
    a2 = make_article("a2", "C1")
    articles_by_client = {"C1": [a1, a2]}

    async def fake_judge(client, article):
        if article.id == "a1":
            raise ValueError("boom")
        return ArticleDecision(article_id=article.id, client_id=client.id, same_person=0.9)

    run = await screen(clients, articles_by_client, fake_judge)

    decisions_by_id = {d.article_id: d for d in run.decisions}
    assert decisions_by_id["a1"].error == "exception:ValueError"
    assert decisions_by_id["a2"].error is None


def test_route_run_missing_decision_is_review_fail_closed():
    client = make_client("C1")
    article = make_article("a1", "C1")

    run = ScreenRun(clients=[client], articles=[article], decisions=[], receipts=Receipts())

    t = Thresholds()
    verdicts = verdicts_for_run(run, t)
    assert verdicts["a1"] == (Verdict.REVIEW, "fail-closed: no_decision")

    results = route_run(run, t)
    assert results[0].status == Status.REVIEW


def test_save_and_load_run_round_trip(tmp_path):
    client = make_client("C1")
    article = make_article("a1", "C1")
    decision = ArticleDecision(article_id="a1", client_id="C1", same_person=0.9)
    run = ScreenRun(clients=[client], articles=[article], decisions=[decision], receipts=Receipts())

    path = tmp_path / "r.json"
    save_run(path, run)
    loaded = load_run(path)

    assert loaded == run
