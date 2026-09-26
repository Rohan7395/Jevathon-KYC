# tasks-M4.md

### Task M4.1: Monitor tick

**Goal:** Add a monitor tick that fetches current GDELT articles for the portfolio and screens only articles not already in the stored `ScreenRun`. It merges the new articles and decisions into that run and returns HIT/REVIEW alerts. The UI gets a "Run monitor tick" button and an alert list.

**File(s):**
- `app/gdelt.py` (add a function)
- `app/screening.py` (add functions)
- `app/api.py` (add an endpoint and wiring)
- `static/index.html` (add a button and an alert list)
- `tests/test_monitor.py` (new)

**Contract:**

**Signature(s):**

```python
# app/gdelt.py
async def fetch_new_articles(
    clients: list[Client],
    known_article_ids: set[str],
    http: httpx.AsyncClient,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[list[Article], list[str]]:
    """Returns (new_articles, fetch_failures). fetch_failures = client ids whose fetch failed."""

def article_id_for(client_id: str, url: str) -> str:
    """hashlib.sha1((client_id + url).encode("utf-8")).hexdigest()[:12]"""

# app/screening.py
JEV_PRICE_PER_INPUT_TOKEN: float = 4.2e-8   # import from app/config.py instead if M1.1 already defines it

def add_receipts(a: Receipts, b: Receipts) -> Receipts:
    """Field-wise sum of all six Receipts fields."""

async def screen_new_articles(
    run: ScreenRun,
    new_articles: list[Article],
    judge: Callable[[Client, Article], Awaitable[ArticleDecision]],
    on_decision: Callable[[ArticleDecision], None],
    concurrency: int = 8,
) -> tuple[ScreenRun, list[ArticleDecision], Receipts]:
    """Returns (merged_run, new_decisions, tick_receipts)."""

# app/api.py
def get_run() -> ScreenRun: ...
def set_run(run: ScreenRun) -> None: ...
def _persist_run(run: ScreenRun) -> None: ...   # writes data/results.json = run.model_dump_json(indent=2)
async def _monitor_fetch(clients: list[Client], known_ids: set[str]) -> tuple[list[Article], list[str]]: ...
async def _monitor_judge(client: Client, article: Article) -> ArticleDecision: ...

@app.post("/api/monitor/tick")
async def monitor_tick(strictness: float = 0.5, client_ids: str | None = None) -> dict: ...
```

**Inputs / Outputs** (models from `app/models.py`, do not redefine):

- **`Client`:**
  - `id: str`
  - `name: str`
  - `birth_year`, `city`, `country`, `occupation`, `organization`: all optional
- **`Article`:**
  - `id: str`, equal to `sha1(client_id + url)[:12]`
  - `client_id: str`
  - `url: str`
  - `title: str`
  - `domain: str | None`
  - `seen_date: str | None`
  - `language: str | None`
  - `text: str | None`
  - `title_only: bool`
- **`ArticleDecision`:**
  - `article_id`, `client_id`
  - `same_person`, `is_subject`, `risk_type`, `risk_conf`, `risk_probs`, `severity`, `severity_conf`
  - `error: str | None`. If set, the router returns REVIEW.
  - `latency_ms`, `input_tokens: int`, `model`
- **`Receipts`:**
  - `jev_calls: int`
  - `jev_errors: int`
  - `input_tokens: int`
  - `jev_cost_usd: float`
  - `elapsed_s: float`
  - `llm_equiv_cost_usd: float`
- **`ScreenRun`:**
  - `clients: list[Client]`
  - `articles: list[Article]`
  - `decisions: list[ArticleDecision]`
  - `receipts: Receipts`

**`fetch_new_articles` behavior:**

For each client in list order:

1. `GET https://api.gdeltproject.org/api/v2/doc/doc?query="{NAME}"&mode=artlist&format=json&maxrecords=25&sort=datedesc`
   - Pass these via `params=`, with `query=f'"{client.name}"'`.
   - The response is `{"articles":[{"url","title","seendate","domain","language","sourcecountry"}...]}`.
2. Map each item to an `Article`:
   - `id=article_id_for(client.id, url)`
   - `client_id=client.id`
   - `url=url`
   - `title=title`
   - `domain=domain`
   - `seen_date=seendate` (raw string)
   - `language=language`
   - `text=None`
   - `title_only=True`
3. Keep an article only if all of these hold:
   - `client.name.split()[-1].lower()` is in `title.lower()`.
   - `id not in known_article_ids`.
   - Its `id` has not already been produced in this call.

**`screen_new_articles` behavior:**

1. Drop any article whose `id` is already in `run.articles`.
2. If nothing remains, return `(run, [], Receipts())` without calling `judge`.
3. Otherwise, call `judge(client, article)` for each article concurrently, under `asyncio.Semaphore(concurrency)`.
   - Look the client up by `article.client_id` in `run.clients`.
4. Call `on_decision(d)` once per decision, in `new_articles` order, after all judging finishes.
5. Build `tick_receipts`:
   - `jev_calls = len(new_decisions)`
   - `jev_errors = count(d.error is not None)`
   - `input_tokens = sum(d.input_tokens)`
   - `jev_cost_usd = input_tokens * 4.2e-8`
   - `elapsed_s` = wall-clock seconds of the judging phase only
   - `llm_equiv_cost_usd = run.receipts.llm_equiv_cost_usd * (tick.input_tokens / run.receipts.input_tokens)` if `run.receipts.input_tokens > 0`, else `0.0`
6. Build `merged_run = run.model_copy(update={...})` with:
   - `articles = run.articles + new_articles`
   - `decisions = run.decisions + new_decisions`
   - `receipts = add_receipts(run.receipts, tick_receipts)`

   `clients` is unchanged. Never mutate the input `run`.

**`POST /api/monitor/tick` response (200):**

```json
{
  "new_articles": 3,
  "alerts": [{"client_id": "...", "client_name": "...", "article_id": "...", "title": "...", "url": "...", "verdict": "HIT", "reason": "fraud, severity 2"}],
  "fetch_failures": ["c07"],
  "tick_receipts": { "...Receipts fields..." },
  "receipts": { "...cumulative Receipts fields after merge..." }
}
```

**Endpoint steps:**

1. Acquire a module-level `asyncio.Lock`.
2. Call `run = get_run()`.
3. Select clients.
   - All clients if `client_ids` is None.
   - Otherwise the comma-separated ids that exist in `run.clients`.
4. Build `known = {a.id for a in run.articles}`.
5. Call `_monitor_fetch(selected, known)`.
6. Call `screen_new_articles(run, new_articles, _monitor_judge, on_decision=<audit append>)`.
7. Call `set_run(merged)`, then `_persist_run(merged)`.
8. Build alerts:
   - Set `t = thresholds_from_strictness(strictness)`.
   - For each new decision `d`, compute `(v, reason) = verdict_for_article(d, t)`.
   - Include the article only if `v in (Verdict.HIT, Verdict.REVIEW)`.
   - Order alerts HIT first, then REVIEW, each group in fetch order.
9. Return the response.

**Errors:**

- **GDELT non-JSON** (`not r.text.lstrip().startswith("{")`):
  1. `logger.warning` with the client id and `r.text[:120]`.
  2. `await sleep(10)`, then retry once.
  3. If it's still non-JSON, append `client.id` to `fetch_failures` and move on to the next client.
  4. Never treat it as zero articles silently.
- **GDELT `httpx.HTTPError` or JSON decode error:** same path as non-JSON (warn, wait 10s, retry once, then record the failure). `fetch_new_articles` never raises.
- **`judge` raises:** catch the exception and `logger.exception` it, then substitute `ArticleDecision(article_id=a.id, client_id=a.client_id, error=f"exception:{type(e).__name__}")`. This fails closed to REVIEW and counts as a Jev error.
- **`on_decision` raises:** let it propagate. Audit writes must not be silently lost.
  - The endpoint returns 500.
  - Nothing is merged: `set_run` and `_persist_run` are not called, because merging happens after auditing.
- **Endpoint:**
  - Lock already held → HTTP 409 `{"detail": "monitor tick already running"}`.
  - `client_ids` given but none match → HTTP 400 `{"detail": "no matching clients"}`.

**Depends on:**

- **M1.1:** `Client`, `Article`, `ArticleDecision`, `Receipts`, `ScreenRun`, `Verdict` from `app/models.py`; the price constant from `app/config.py` if present.
- **M1.3:**
  - The jev_client public async function that judges one `(Client, Article)` pair into an `ArticleDecision` and never raises.
  - `_monitor_judge` wraps it, using one shared module-level `httpx.AsyncClient`.
  - RESOLVED: if its name or signature differs from what you expect, only `_monitor_judge` adapts.
- **M1.4:**
  - `verdict_for_article(d: ArticleDecision, t: Thresholds) -> tuple[Verdict, str]`
  - `thresholds_from_strictness(s: float) -> Thresholds` (clamps `s` to [0, 1])
- **M1.5:** the audit.py append-one-decision-to-`data/audit.jsonl` function, wired as `on_decision`. RESOLVED: adapt only that wiring line to its name.
- **M1.6:** the `ScreenRun` layout in `data/results.json`.
- **M2.1:**
  - The in-memory `ScreenRun` that `GET /api/results` re-routes from.
  - RESOLVED: if M2.1 stores it in a module-level variable, add the `get_run` / `set_run` accessors around that variable. `GET /api/results` must read the same object.
- **M2.2 / M2.3:** the existing receipts bar, and the function the Dial uses to reload `/api/results`.

**Implementation constraints:**

- **Throttle:** about 1 GDELT request every 5 seconds. Call `await sleep(5)` between consecutive client requests, but not before the first one. The 10s retry wait replaces the 5s gap for that retry.
- **RESOLVED ("since the last tick"):**
  - An article is new if its `article_id` is not already in the stored `ScreenRun`.
  - No date comparison is done. Jev can't do dates, and GDELT `seendate` is stored raw.
  - No new GDELT query parameters are added.
- **RESOLVED (article text):** monitor articles are title-only.
  - `text=None` and `title_only=True`.
  - No trafilatura body fetch.
- **RESOLVED (article ID):** use `sha1(client_id + url)`, UTF-8, with no separator.
  - If `app/gdelt.py` already has an article-id helper from M1.2, `article_id_for` must return an identical value. Reuse that helper.
- **RESOLVED (last name):** the last name for the prefilter is `client.name.split()[-1]`.
- **RESOLVED (duration):** a full-portfolio tick takes about N×5s because of the throttle.
  - `client_ids` scopes a tick to specific clients.
  - The UI button always ticks all clients.
- **RESOLVED (persistence):** the tick persists by rewriting `data/results.json`. `ScreenRun` is the only stored shape, and verdicts and statuses are never stored.
- **GDELT HTTP client:** `_monitor_fetch` creates `httpx.AsyncClient(timeout=20)` per tick and calls `fetch_new_articles`.
- **No new dependencies. Do not modify any model, threshold, or router function.**
- **UI** (`static/index.html`, vanilla JS):
  - Add a "Run monitor tick" button next to the receipts bar, and a `<ul id="alerts">` section headed "Monitor alerts".
  - **On click:**
    - Disable the button and show "Running monitor tick…".
    - `POST /api/monitor/tick?strictness=<current Dial value>`.
  - **On 200:**
    - Render one `<li>` per alert: a verdict chip (reuse the status-chip styles), the client name, the title as `<a href=url target="_blank" rel="noopener">`, and the reason.
    - If there are no alerts, render "No new alerts".
    - Show "+{tick_receipts.jev_calls} Jev calls".
    - If `fetch_failures` is non-empty, show "GDELT failed for: {ids}".
    - Then call the existing Dial reload function, so the receipts bar, chips, and table refresh.
  - **On 409:** show "Tick already running".
  - **On other errors:** show "Monitor tick failed: {status}".
  - Always re-enable the button.

**Acceptance tests** (`tests/test_monitor.py`; all HTTP mocked, with `httpx.MockTransport` for GDELT and fake async callables for judge and fetch; `pytest-asyncio`):

1. **Filtering and mapping in `fetch_new_articles`:**
   - Setup:
     - Client `Client(id="c1", name="Jane Doe")`.
     - The mock returns JSON with 3 articles: (a) title "Jane Doe charged", whose id is in `known_article_ids`; (b) title "Unrelated story"; (c) title "DOE firm raided", url `https://x.com/2`, `seendate` `"20260926T120000Z"`, `domain` `"x.com"`.
     - `sleep` is an `AsyncMock`.
   - Expected:
     - Returns exactly one `Article`: `id == article_id_for("c1","https://x.com/2")`, `title_only is True`, `text is None`, `seen_date == "20260926T120000Z"`.
     - `fetch_failures == []`.
2. **Non-JSON handling in `fetch_new_articles`:**
   - Setup: two clients. The mock returns `200 "Please limit requests"` (plain text) on every call for c1, and valid JSON with zero articles for c2.
   - Expected:
     - Returns `([], ["c1"])`.
     - `sleep` was awaited with `10` once and `5` once.
     - One warning was logged containing `"Please limit requests"` (checked with `caplog`).
     - c2 is not in the failures.
3. **Merging and receipts in `screen_new_articles`:**
   - Setup:
     - `run` has 1 client, 1 existing article and decision, and `receipts=Receipts(jev_calls=5, input_tokens=1000, llm_equiv_cost_usd=0.5)`.
     - Two new articles.
     - `judge` returns `ArticleDecision(..., input_tokens=400)` for the first and raises `RuntimeError` for the second.
     - `on_decision` is a list-append.
   - Expected:
     - `merged.articles` has length 3 and `merged.decisions` has length 3.
     - The second new decision has `error == "exception:RuntimeError"`.
     - `tick_receipts.jev_calls == 2`, `jev_errors == 1`, `input_tokens == 400`.
     - `jev_cost_usd == pytest.approx(400 * 4.2e-8)` and `llm_equiv_cost_usd == pytest.approx(0.2)`.
     - `merged.receipts.jev_calls == 7`.
     - `on_decision` was called 2 times.
     - The input `run.articles` still has length 1.
   - Also, with `new_articles=[]`, it returns `(run, [], Receipts())` and `judge` is never called.
4. **End-to-end API tick:**
   - Setup:
     - `set_run(fixture_run)`, where the fixture has 2 clients and 0 articles.
     - Monkeypatch `app.api._persist_run` to a no-op, the audit append to a list-append, and `app.api._monitor_fetch` to return 2 new articles for c1.
     - Monkeypatch `app.api._monitor_judge` to return:
       - article A: `same_person=0.95, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2, severity_conf=0.8, risk_probs={"fraud":0.9}`
       - article B: `same_person=0.1, is_subject=0.1, risk_type="none", risk_conf=0.9, severity=0, severity_conf=0.9`
   - `TestClient.post("/api/monitor/tick")` expected:
     - 200.
     - `new_articles == 2`.
     - `alerts` has exactly 1 entry: `verdict == "HIT"`, `reason == "fraud, severity 2"`.
     - `GET /api/results` then shows `receipts.jev_calls == tick_receipts.jev_calls == 2`.
     - The audit list has 2 entries.
5. **Idempotence and scoping:**
   - A second identical tick, where the fetch fake respects `known_ids`, returns `new_articles == 0` and `alerts == []`, and cumulative `receipts.jev_calls` is unchanged.
   - `POST /api/monitor/tick?client_ids=zzz` returns 400.

**Done when:** `pytest -q tests/test_monitor.py` passes, and a manual check works.

**Manual check:**
1. With `uvicorn app.api:app --port 8000` running, click "Run monitor tick".
2. The alert list shows HIT/REVIEW items or "No new alerts".
3. The receipts bar's Jev calls rises by exactly the "+N Jev calls" shown.