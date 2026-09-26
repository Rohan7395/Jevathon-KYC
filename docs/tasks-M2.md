# tasks-M2.md — KYC Sentinel · M2: API + UI

---

### Task M2.1: API endpoints + static serving

Goal: Build a FastAPI app that serves the stored `ScreenRun` (`data/results.json`), re-routed through the pure router at any strictness, with zero Jev calls. It also exposes per-client detail, a reload/rescreen trigger, and serves `static/index.html` at `/`.

File(s):
- `app/api.py` (new)
- `static/index.html` (new, placeholder only: `<!doctype html><html><head><title>KYC Sentinel</title></head><body><h1>KYC Sentinel</h1></body></html>`; M2.2 overwrites it)
- `tests/test_api.py` (new)

Contract:
  Signature(s):
  ```python
  from pathlib import Path
  from typing import Any, Literal
  from fastapi import FastAPI, HTTPException, Query
  from fastapi.responses import FileResponse
  from app.models import (Article, ArticleDecision, Client, ClientResult, ScreenRun,
                          Status, Thresholds, Verdict)
  from app.router import verdict_for_article, status_for_client, thresholds_from_strictness

  ROOT: Path = Path(__file__).resolve().parent.parent
  RESULTS_PATH: Path = ROOT / "data" / "results.json"
  STATIC_DIR: Path = ROOT / "static"
  app: FastAPI = FastAPI(title="KYC Sentinel")
  _RUN: ScreenRun | None = None   # module-level in-memory cache

  def load_run(path: Path) -> ScreenRun
  def get_run() -> ScreenRun
  def reset_cache() -> None
  def route_client(run: ScreenRun, client: Client, t: Thresholds
                   ) -> tuple[ClientResult, dict[str, tuple[Verdict, str]], dict[str, ArticleDecision], list[Article]]
  def build_results(run: ScreenRun, strictness: float) -> dict[str, Any]
  def build_client_detail(run: ScreenRun, client_id: str, strictness: float) -> dict[str, Any] | None

  @app.get("/api/results")
  def get_results(strictness: float = Query(0.5)) -> dict[str, Any]
  @app.get("/api/client/{client_id}")
  def get_client(client_id: str, strictness: float = Query(0.5)) -> dict[str, Any]
  @app.post("/api/screen")
  def post_screen(mode: Literal["reload", "rescreen"] = Query("reload")) -> dict[str, Any]
  @app.get("/")
  def index() -> FileResponse
  ```
  All endpoints are sync `def`, so FastAPI runs them in its threadpool.

  Inputs / Outputs (models from `app/models.py`; relevant fields copied here):
  - `ScreenRun { clients: list[Client]; articles: list[Article]; decisions: list[ArticleDecision]; receipts: Receipts }`. Verdicts and statuses are never stored; they are always recomputed by the router.
  - `Client { id, name, birth_year, city, country, occupation, organization }`
  - `Article { id, client_id, url, title, domain, seen_date, language, text, title_only: bool }`. `id` is globally unique.
  - `ArticleDecision { article_id, client_id, same_person, is_subject, risk_type, risk_conf, risk_probs, severity, severity_conf, error, latency_ms, input_tokens, model }`
  - `ClientResult { client_id, status: Status, reasons: list[str], article_verdicts: dict[str, Verdict], worst_article_id }`
  - `Receipts { jev_calls, jev_errors, input_tokens, jev_cost_usd, elapsed_s, llm_equiv_cost_usd }`
  - `load_run(path)` returns `ScreenRun.model_validate_json(path.read_text(encoding="utf-8"))`.
  - `get_run()` returns `_RUN`, calling `load_run(RESULTS_PATH)` first if `_RUN is None`.
  - `reset_cache()` sets `_RUN = None`. It is used by tests.
  - `route_client(run, client, t)`:
    1. `arts` = articles with `a.client_id == client.id`, in `run.articles` order.
    2. `decisions` = `{a.id: <decision whose article_id == a.id>}` for each article.
    3. `verdicts` = `{aid: verdict_for_article(d, t)}`.
    4. `result` = `status_for_client(client.id, verdicts, decisions, t)`.
    5. Returns `(result, verdicts, decisions, arts)`.
  - `build_results(run, strictness)` → JSON:
    ```json
    {
      "strictness": <float clamped to [0,1]>,
      "thresholds": <Thresholds.model_dump()>,
      "clients": [ { ...Client.model_dump(), ...ClientResult.model_dump(mode="json"),
                     "reason": "<reasons[0] or ''>", "article_count": <int> } ],
      "counts": {"CLEAR": n, "REVIEW": n, "FLAGGED": n, "NO_COVERAGE": n},
      "receipts": <run.receipts.model_dump()>
    }
    ```
    - `t = thresholds_from_strictness(strictness)`.
    - `clients` follow `run.clients` order.
    - `counts` always contains all four keys, including zeros.
    - `receipts` is returned unchanged from the stored run.
  - `build_client_detail(run, client_id, strictness)` → `None` if no client has `id == client_id`; otherwise:
    ```json
    {
      "client": <Client.model_dump()>,
      "result": <ClientResult.model_dump(mode="json")>,
      "articles": [ { ...Article.model_dump(), "verdict": "HIT|REVIEW|CLEAR", "reason": "<str>",
                      "decision": <ArticleDecision.model_dump()> } ]
    }
    ```
    `articles` are sorted stably by verdict rank: HIT=0, REVIEW=1, CLEAR=2.
  - `post_screen`:
    - **`mode="reload"`:** `_RUN = load_run(RESULTS_PATH)`.
    - **`mode="rescreen"`:** run `subprocess.run([sys.executable, "scripts/run_screen.py"], cwd=ROOT, capture_output=True, text=True, timeout=600)`, then reload as above.
    - Returns `{"ok": true, "mode": mode, "receipts": <receipts dump>}`.
  - `index()` returns `FileResponse(STATIC_DIR / "index.html", media_type="text/html")`.

  Errors:
  - **`results.json` missing or invalid** (`FileNotFoundError` / `pydantic.ValidationError` / `json` error in `get_run` or reload):
    - Log at ERROR.
    - Raise `HTTPException(503, detail="results.json not available — run scripts/run_screen.py (<exc short msg>)")`.
    - On a failed reload, `_RUN` keeps its previous value (assign only after a successful load).
  - **Unknown client id:** `HTTPException(404, detail="client not found")`.
  - **Rescreen failure** (`returncode != 0` or `subprocess.TimeoutExpired`):
    - Log at ERROR with the last 500 chars of stderr.
    - Raise `HTTPException(500, detail="rescreen failed: <last 300 chars of stderr or 'timeout'>")`.
    - `_RUN` is unchanged.
  - **Non-numeric strictness or invalid mode:** FastAPI's default 422.
  - **Out-of-range strictness:** not an error; `thresholds_from_strictness` clamps. The echoed `"strictness"` is `max(0.0, min(1.0, s))`.

  Depends on:
  - M1.1: `app.models` (all models above).
  - M1.4:
    - `verdict_for_article(d: ArticleDecision, t: Thresholds) -> tuple[Verdict, str]`
    - `status_for_client(client_id: str, verdicts: dict[str, tuple[Verdict, str]], decisions: dict[str, ArticleDecision], t: Thresholds) -> ClientResult`
    - `thresholds_from_strictness(s: float) -> Thresholds`
  - M1.6: `scripts/run_screen.py` writes `data/results.json` as a `ScreenRun` and exits non-zero if its health-check assert fails.

Implementation constraints:
- Never import or call `app.jev_client`, `app.screening`, or `app.gdelt` from `api.py`. The Dial re-routes stored decisions with **zero new Jev calls**.
- RESOLVED (status_for_client input): pass only this client's articles in `verdicts`/`decisions`. An empty dict means NO_COVERAGE.
- RESOLVED (article with no decision): synthesize `ArticleDecision(article_id=a.id, client_id=client.id, error="missing_decision")`. It fails closed to REVIEW via the router.
- RESOLVED (`POST /api/screen`): implemented as a subprocess of `scripts/run_screen.py`, so it does not depend on M1.6's internal function names. The default mode is `reload`.
- RESOLVED (`/api/client` strictness): the endpoint accepts `strictness` so drawer verdicts match the Dial.
- RESOLVED (static serving): use an explicit `GET /` FileResponse. There is no `/static` mount; the page is one self-contained file.
- Router rules, for reference only (do not reimplement):
  ```
  verdict_for_article:
    error or any of (same_person, is_subject, risk_type, risk_conf, severity) is None → REVIEW, "fail-closed: <error or missing field>"
    same_person < t.same_person_clear → CLEAR, "different person"
    same_person >= t.same_person_hit and is_subject < t.not_subject_clear → CLEAR, "not the subject (victim/bystander)"
    risk_type == "none" and risk_conf >= t.none_clear_conf → CLEAR, "no wrongdoing"
    same_person >= t.same_person_hit and is_subject >= t.is_subject_hit and risk_type != "none" and risk_conf >= t.hit_risk_conf → HIT, "<risk_type>, severity <n>"
    else → REVIEW, "uncertain"
  status_for_client: no articles → NO_COVERAGE; any HIT with same_person >= t.flag_same_person AND severity >= 1 → FLAGGED; any HIT or REVIEW → REVIEW; else CLEAR
  thresholds_from_strictness (linear, s clamped to [0,1]; HIT-side fixed: same_person_hit 0.70, is_subject_hit 0.60, hit_risk_conf 0.70, flag_same_person 0.85):
    same_person_clear: 0.40 (s=0) → 0.10 (s=1)
    not_subject_clear: 0.30 (s=0) → 0.10 (s=1)
    none_clear_conf:   0.55 (s=0) → 0.90 (s=1)
  ```
- Log at INFO on each successful load: `loaded ScreenRun: <n> clients, <n> articles, <n> decisions`.

Acceptance tests (in `tests/test_api.py`; M2.2 and M2.3 will append to this file, so keep these fixture names):

Fixture `sample_run() -> ScreenRun`:
- **Receipts:** `Receipts(jev_calls=4, jev_errors=1, input_tokens=1600, jev_cost_usd=0.0000672, elapsed_s=3.2, llm_equiv_cost_usd=0.012)`
- **Clients:**
  - `c1` "Jane Doe"
  - `c2` "Michael Jordan" (b.1982, Columbus, dentist)
  - `c3` "John Smith" (b.1980, Denver, software engineer)
  - `c4` "Ana Ruiz"
  - `c5` "Wei Chen"
- **Articles:** `a1→c1`, `a2→c2`, `a3→c3`, `a5→c5`. Each has `url=f"https://ex.com/{id}"`, `title=f"Title {id}"`, `domain="ex.com"`, `text=None`, `title_only=True`. `c4` has no articles.
- **Decisions:**
  - `a1`: same_person 0.95, is_subject 0.90, risk_type "fraud", risk_conf 0.90, severity 2
  - `a2`: same_person 0.05, is_subject 0.90, risk_type "fraud", risk_conf 0.80, severity 3
  - `a3`: same_person 0.20, is_subject 0.50, risk_type "none", risk_conf 0.60, severity 0
  - `a5`: `error="timeout"`, all probability fields None

Fixture `api_client(tmp_path, monkeypatch, sample_run) -> TestClient`:
1. Writes `sample_run.model_dump_json()` to `tmp_path/"results.json"`.
2. Monkeypatches `app.api.RESULTS_PATH` to that file.
3. Calls `app.api.reset_cache()`.
4. Returns `TestClient(app.api.app)`.

Tests:
1. `test_results_default` — `GET /api/results`:
   - `counts == {"CLEAR": 2, "REVIEW": 1, "FLAGGED": 1, "NO_COVERAGE": 1}`
   - c1 is `FLAGGED` with `reason == "fraud, severity 2"`
   - c2 is `CLEAR` with `reason == "different person"`
   - c4 is `NO_COVERAGE`
   - c5 is `REVIEW` with a reason that starts with `"fail-closed"`
   - `receipts["jev_calls"] == 4`
   - clients are in order c1..c5
2. `test_dial_reroutes_without_jev` — first monkeypatch `httpx.AsyncClient.post` to raise `AssertionError`.
   - `?strictness=0.0`: counts are CLEAR 2 / REVIEW 1 / FLAGGED 1 / NO_COVERAGE 1.
   - `?strictness=1.0`: counts are CLEAR 1 / REVIEW 2 / FLAGGED 1 / NO_COVERAGE 1, and c3 is `REVIEW` with `"uncertain"`.
   - `?strictness=5`: returns `"strictness": 1.0` with the same counts as 1.0.
   - `receipts` are identical across all calls.
3. `test_client_detail`:
   - `GET /api/client/c2` → `articles[0]` has verdict `"CLEAR"`, reason `"different person"`, `decision.same_person == 0.05`, and `title_only is True`.
   - `GET /api/client/c3?strictness=1.0` → `articles[0].verdict == "REVIEW"`.
   - `GET /api/client/nope` → 404.
4. `test_missing_results_and_index`:
   - Monkeypatch `RESULTS_PATH` to a nonexistent file and call `reset_cache()`. `GET /api/results` → 503 with `"run_screen.py"` in `detail`.
   - `GET /` → 200 with `text/html` content-type.
5. `test_screen_reload_and_rescreen`:
   - Rewrite the results file with `jev_calls=9`. `POST /api/screen` → 200, and a subsequent `GET /api/results` shows `receipts.jev_calls == 9`.
   - Monkeypatch `subprocess.run` (as used in `app.api`) to return `CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")`. `POST /api/screen?mode=rescreen` → 500 with `"boom"` in `detail`, and `GET /api/results` still shows 9.
   - Monkeypatch it to write `jev_calls=12` and return returncode 0. `POST /api/screen?mode=rescreen` → 200 with `receipts.jev_calls == 12`.

Done when: `pytest -q tests/test_api.py` passes, and after `uvicorn app.api:app --port 8000`, `curl "localhost:8000/api/results?strictness=1.0"` returns JSON with `clients`, `counts`, and `receipts` matching the M1 CLI run.

---

### Task M2.2: Dashboard page

Goal: Replace the placeholder `static/index.html` with a single-file vanilla-JS dashboard. It shows:
- a receipts bar
- the Autonomy Dial (a debounced slider calling `/api/results`)
- four count chips
- a client table with status chips

Moving the Dial re-renders counts and statuses instantly.

File(s):
- `static/index.html` (overwrite)
- `tests/test_api.py` (append tests only; do not modify existing tests or fixtures)

Contract:
  Signature(s) (JS, in one inline `<script>`):
  ```js
  let currentStrictness = 0.5;       // number in [0,1]
  let openClientId = null;           // string | null — used by M2.3
  let resultsSeq = 0;                // stale-response guard
  function debounce(fn, ms) { ... }  // returns debounced fn
  async function loadResults(strictness) { ... }   // GET /api/results?strictness=<s>, then render
  function renderReceipts(receipts) { ... }
  function renderCounts(counts) { ... }
  function renderTable(clients) { ... }
  function showError(msg) { ... }    // msg: string | null (null hides banner)
  function openDrawer(clientId) { /* stub — implemented by M2.3 */ }
  ```

  Inputs / Outputs — this is the API response from M2.1, `GET /api/results?strictness=<float>`:
  ```json
  {
    "strictness": 0.5,
    "thresholds": {...},
    "clients": [ { "id": "c1", "name": "...", "birth_year": 1982, "city": "...", "country": "...",
                   "occupation": "...", "organization": "...",
                   "client_id": "c1", "status": "CLEAR|REVIEW|FLAGGED|NO_COVERAGE",
                   "reasons": ["..."], "article_verdicts": {"<article_id>": "HIT|REVIEW|CLEAR"},
                   "worst_article_id": "... or null", "reason": "<first reason or ''>", "article_count": 3 } ],
    "counts": {"CLEAR": 0, "REVIEW": 0, "FLAGGED": 0, "NO_COVERAGE": 0},
    "receipts": {"jev_calls": 0, "jev_errors": 0, "input_tokens": 0, "jev_cost_usd": 0.0,
                 "elapsed_s": 0.0, "llm_equiv_cost_usd": 0.0}
  }
  ```
  On a non-2xx response, the body is `{"detail": "<message>"}`.

  Required DOM (exact ids and attributes; M2.3 and the tests rely on them):
  - **Receipts bar:** `<div id="receipts">`. Text: `Jev calls: N | errors: E | tokens: T | Jev $: X | elapsed: Ys | LLM est $: Z (estimate)`. X and Z use `toFixed(4)`, Y uses `toFixed(1)`, T uses `toLocaleString()`.
  - **Dial:**
    - `<input type="range" id="dial" min="0" max="1" step="0.05" value="0.5">`
    - label text "Autonomy Dial (strictness)"
    - end labels "lenient" (left) and "strict" (right)
    - `<span id="dial-value">0.50</span>`
  - **Count chips:** `<div id="counts">` containing exactly four chips in this order: `<span class="chip" data-status="CLEAR">CLEAR <b>n</b></span>`, then the same for `REVIEW`, `FLAGGED`, `NO_COVERAGE`.
  - **Error banner:** `<div id="error" hidden>`.
  - **Client table:** `<table id="client-table">` with header columns `Client | Status | Reason | Articles` and a `<tbody>`. Each row is `<tr data-client-id="<id>">` containing:
    - the name in bold, plus a muted line: occupation · city, country (skip nulls)
    - a status chip `<span class="chip" data-status="<status>">`
    - `reason`
    - `article_count`

  Behavior:
  - On `DOMContentLoaded`, call `loadResults(0.5)`.
  - On the Dial's `input` event:
    - Immediately set `currentStrictness` and `#dial-value` (`toFixed(2)`).
    - Call `loadResults(currentStrictness)` through `debounce(…, 200)`.
  - `loadResults`:
    - Increment `resultsSeq` and capture it.
    - After the fetch, discard the response if `resultsSeq` has changed.
    - Otherwise render receipts, counts, and table, then call `if (openClientId) openDrawer(openClientId);`.
  - Row click: `openClientId = <id>; openDrawer(<id>);`.
  - Chip colors: CLEAR green, REVIEW amber, FLAGGED red, NO_COVERAGE gray. Row cursor is pointer.

  Errors:
  - Fetch rejects or the response is non-2xx → `showError(detail || "request failed: <status>")`. Keep the last good render on screen; never clear the table on error.
  - A successful load calls `showError(null)`.

  Depends on: M2.1 — `GET /`, `GET /api/results`, and the fixture `api_client` in `tests/test_api.py`.

Implementation constraints:
- There is one file with inline `<style>` and `<script>`. Do not use external scripts, stylesheets, fonts, frameworks, or a build step.
- Build DOM from data with `createElement`/`textContent`. Never pass API strings through `innerHTML`.
- Do not implement the drawer; leave `openDrawer` as an empty stub (M2.3 replaces its body).
- Do not add a rescreen button or any feature not listed.

Acceptance tests (append to `tests/test_api.py`, using the existing `api_client` fixture):
1. `test_index_has_dashboard_elements` — `GET /` → 200, and the body contains `id="receipts"`, `id="dial"`, `id="dial-value"`, `id="counts"`, `id="client-table"`, `id="error"`.
2. `test_index_has_chips_and_wiring` — the body contains `data-status="CLEAR"`, `data-status="REVIEW"`, `data-status="FLAGGED"`, `data-status="NO_COVERAGE"`, `/api/results?strictness=`, `function openDrawer`, and `data-client-id`.
3. `test_index_no_external_assets` — the body does not contain `<script src=` or `rel="stylesheet"`.

Done when: tests pass, and at `localhost:8000`, dragging the Dial from 0 to 1 makes CLEAR fall and REVIEW rise while FLAGGED and "Jev calls" stay unchanged.

---

### Task M2.3: Client drawer

Goal: Clicking a client row opens a side drawer, loaded from `/api/client/{id}?strictness=<current>`. It lists each article with its verdict, reason, Jev fields, and a title-only badge. The drawer stays in sync when the Dial moves.

File(s):
- `static/index.html` (edit: add drawer markup, CSS, and the `openDrawer`/`closeDrawer` logic)
- `tests/test_api.py` (append tests only)

Contract:
  Signature(s) (JS, in the existing inline `<script>`):
  ```js
  // existing globals from M2.2 (do not rename): currentStrictness (number), openClientId (string|null)
  let drawerSeq = 0;                          // stale-response guard
  async function openDrawer(clientId) { ... } // replaces the M2.2 stub body
  function closeDrawer() { ... }
  function renderDrawer(detail) { ... }
  function fmtProb(x) { ... }                 // number → x.toFixed(2); null/undefined → "—"
  const SEVERITY_LABELS = ["Rumor or unverified allegation", "Formal investigation, raid, or lawsuit",
                           "Criminally charged or indicted", "Convicted, sanctioned, or penalized"];
  ```

  Inputs / Outputs — this is the API response from M2.1, `GET /api/client/{id}?strictness=<float>`:
  ```json
  {
    "client": {"id","name","birth_year","city","country","occupation","organization"},
    "result": {"client_id","status","reasons":[...],"article_verdicts":{...},"worst_article_id"},
    "articles": [ { "id","client_id","url","title","domain","seen_date","language","text","title_only": true,
                    "verdict": "HIT|REVIEW|CLEAR", "reason": "...",
                    "decision": {"same_person","is_subject","risk_type","risk_conf","risk_probs",
                                 "severity","severity_conf","error","latency_ms","input_tokens","model"} } ]
  }
  ```
  - Articles arrive sorted HIT → REVIEW → CLEAR.
  - Any probability field may be null.
  - A 404 response is `{"detail": "client not found"}`.

  Existing M2.2 contract (already in the file; do not change it):
  - Row click sets `openClientId` then calls `openDrawer(id)`.
  - After every Dial re-render, M2.2 calls `openDrawer(openClientId)` if a drawer is open.
  - Chip style is `<span class="chip" data-status="...">`, colored CLEAR green, REVIEW amber, FLAGGED red, NO_COVERAGE gray.

  Required DOM (exact ids):
  ```html
  <aside id="drawer" hidden>
    <button id="drawer-close" aria-label="Close">×</button>
    <h2 id="drawer-title"></h2>
    <div id="drawer-meta"></div>
    <div id="drawer-status"></div>
    <ul id="drawer-reasons"></ul>
    <div id="drawer-actions"></div>
    <ul id="drawer-articles"></ul>
  </aside>
  ```
  - `#drawer-actions` stays empty; M3.3 mounts the memo button there.
  - `#drawer-meta` shows birth_year · city, country · occupation · organization, skipping nulls.
  - `#drawer-status` shows a status chip.

  Behavior:
  - **Opening:** `openDrawer(clientId)`:
    1. Set `openClientId = clientId`.
    2. Unhide the drawer.
    3. Increment and capture `drawerSeq`.
    4. Fetch `/api/client/${encodeURIComponent(clientId)}?strictness=${currentStrictness}`.
    5. Discard the result if `drawerSeq` changed or `openClientId !== clientId`; otherwise call `renderDrawer`.
  - **Article list items:** each `<li data-article-id="<id>">` shows, in order:
    1. A verdict chip `<span class="chip" data-verdict="HIT|REVIEW|CLEAR">`, colored HIT red, REVIEW amber, CLEAR green.
    2. The title as `<a href=url target="_blank" rel="noopener">`.
    3. A muted line: `domain · seen_date`.
    4. `<span class="badge title-only">title only</span>` when `title_only` is true.
    5. The reason.
    6. A Jev line: `same_person <fmtProb> · is_subject <fmtProb> · risk <risk_type or "—"> (<fmtProb(risk_conf)>) · severity <n> – <SEVERITY_LABELS[n]>`, or `severity —` if null.
    7. If `decision.error` is set: `Jev error: <error>` in red.
  - **No articles:** show a single `<li>` reading `No articles (NO_COVERAGE)`.
  - **Closing:** `closeDrawer()` sets `openClientId = null`, hides the drawer, and increments `drawerSeq`. It is triggered by `#drawer-close` click and the `Escape` key.

  Errors:
  - Fetch rejects or the response is non-2xx → render `<li class="error">` with `detail || "request failed: <status>"` inside `#drawer-articles`. Keep the drawer open; nothing is thrown.

  Depends on:
  - M2.1: `GET /api/client/{client_id}`, fixture `api_client`.
  - M2.2: globals `currentStrictness` and `openClientId`, the `openDrawer` stub, row `data-client-id`, and the `.chip` CSS.

Implementation constraints:
- All data goes in via `createElement`/`textContent` (and `a.href` for links). Never use `innerHTML` with API strings.
- The drawer is fixed on the right, about 480px wide, full height, with `overflow-y: auto`. It is inline CSS only.
- Do not change any M2.2 ids, the stale guard, or the table rendering.
- Do not add a memo button (that is M3.3).

Acceptance tests (append to `tests/test_api.py`, using the existing `api_client` fixture; the fixture has c2 = collision client with article `a2` at same_person 0.05, and c5 = client whose article `a5` has `error="timeout"`):
1. `test_index_has_drawer_elements` — `GET /` body contains `id="drawer"`, `id="drawer-close"`, `id="drawer-title"`, `id="drawer-articles"`, `id="drawer-actions"`, `/api/client/`, and `title only`.
2. `test_drawer_payload_collision` — `GET /api/client/c2`:
   - `result.status == "CLEAR"`
   - `articles[0]` has `verdict == "CLEAR"`, `reason == "different person"`, and `title_only is True`
   - `articles[0].decision` has the keys `same_person`, `is_subject`, `risk_type`, `risk_conf`, `severity`
3. `test_drawer_payload_error_article` — `GET /api/client/c5` → `articles[0].verdict == "REVIEW"`, `articles[0].decision.error == "timeout"`, and `articles[0].decision.same_person is None`.

Done when: tests pass, and at `localhost:8000`, clicking a collision client opens the drawer showing its articles as CLEAR with "different person", Jev fields, and a title-only badge. Moving the Dial with the drawer open refreshes it; × or Esc closes it.