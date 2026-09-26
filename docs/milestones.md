# milestones.md — KYC Sentinel

**Schedule (now ≈ 1:00 PM, freeze 2:15 PM)**

| Milestone | Window | Priority |
|---|---|---|
| M1 | 1:00 → 1:35 | MUST |
| M2 | 1:35 → 1:55 | MUST |
| M3 | 1:55 → 2:10 | SHOULD |
| M4 | only if M1–M3 are done by 2:05 | BONUS |
| Freeze | 2:15 → 2:30: rehearse, commit, submit. No new code. | — |

---

### M1: Screening Core (CLI only)  [MUST]  [1:00 → 1:35, 35 min]

**What exists at the end**

The whole screening pipeline runs from the command line. A portfolio CSV goes in and news is fetched once from GDELT and cached. Every (client, article) pair is judged by one concurrent Jev call, with a semaphore of 8. Jev failures never raise; they become `ArticleDecision.error`, and those articles go to REVIEW. Raw probabilities are stored in `results.json` as a `ScreenRun`. A pure, exhaustively tested router turns decisions into per-client statuses. Every decision is appended to `audit.jsonl`. A receipts line reports calls, errors, tokens, Jev $, elapsed time, and the LLM-equivalent estimate. A health check fails loudly if 20% or more of Jev calls errored.

**Integration demo**

1. `pip install -r requirements.txt && cp .env.example .env`, then fill in the Jev and LLM keys.
2. `python scripts/fetch_news.py`
   - Expect a throttled per-client progress line (about 1 request every 5s), then `data/news_cache.json` written.
   - Any `fetch_failures` are printed at the end.
   - Non-JSON GDELT responses are logged as warnings with the first 120 chars; they are never counted as zero articles.
3. `python scripts/run_screen.py`
   - The raw JSON of the first Jev response is logged at INFO.
   - A table `client | status | reason` is printed. Collision clients show `CLEAR | different person`. Documented cases show `FLAGGED | <risk_type>, severity <n>`.
   - A receipts line follows: `Jev calls: N | errors: E | tokens: T | Jev $: X | elapsed: Ys | LLM est $: Z`.
   - The health assert passes.
   - `data/results.json` is written and `data/audit.jsonl` grows by N lines.
4. `pytest -q` passes: all router branches, plus Jev client success, 429→retry, and timeout→`error`.

**Files created/modified**

- `app/config.py`
- `app/models.py`
- `app/gdelt.py`
- `app/jev_client.py`
- `app/router.py`
- `app/audit.py`
- `app/receipts.py`
- `app/screening.py`
- `scripts/fetch_news.py`
- `scripts/run_screen.py`
- `data/portfolio.csv`
- `data/news_cache.json` (generated)
- `data/results.json` (generated)
- `data/audit.jsonl` (generated)
- `tests/test_router.py`
- `tests/test_jev_client.py`
- `tests/test_gdelt.py`
- `requirements.txt`
- `.env.example`

**Task list**

| ID | Name | Responsibility | Depends on |
|---|---|---|---|
| M1.1 | Config + Models | Env loading, default thresholds, price constants, and every §4 pydantic model defined once in `models.py`. | — |
| M1.2 | GDELT fetch + cache + `fetch_news.py` + portfolio | Throttled, fail-loud GDELT fetch with non-JSON detection, retry, last-name prefilter, and cache I/O. Includes the 25–40-row `portfolio.csv` in three groups (documented cases, name collisions, common names). | M1.1 |
| M1.3 | Jev client | Async single-call `httpx` client that builds the §5 request and maps the response exactly to `ArticleDecision`. Includes a 10s timeout, one retry on 429/5xx, `missing_field` errors, and first-response logging. Never raises. | M1.1 |
| M1.4 | Router | Pure `verdict_for_article`, `status_for_client`, and `thresholds_from_strictness` per §8. Exhaustive tests, including a test that s=0.5 equals the `Thresholds()` defaults. | M1.1 |
| M1.5 | Audit + Receipts | Append-only JSONL audit writer, plus receipts counters and cost math (Jev $ and LLM-equivalent estimate). | M1.1 |
| M1.6 | Screening orchestrator + `run_screen.py` | Loads the cache, fans out Jev calls with semaphore 8, builds and writes the `ScreenRun`, writes the audit log, prints the status table and receipts, and runs the health-check assert. | M1.2, M1.3, M1.4, M1.5 |

**Cut line**

1. Drop the trafilatura body fetch first; everything runs `title_only=True`.
2. Trim the portfolio to about 25 rows, keeping all ~5 collision clients.
3. Trim the GDELT test to the non-JSON detection case only.

Never cut: fail-closed behavior, router tests, the health check.

---

### M2: API + UI  [MUST]  [1:35 → 1:55, 20 min]

**What exists at the end**

A FastAPI app serves a single vanilla-JS page. The page has:
- a receipts bar
- count chips per status
- a client table with status chips
- the Autonomy Dial, which re-routes stored decisions server-side with zero new Jev calls, so counts shift instantly
- a detail drawer per client, listing each article's verdict, same_person, is_subject, risk_type, severity, and a title-only badge

`POST /api/screen` re-runs screening or reloads from cache.

**Integration demo**

1. `uvicorn app.api:app --port 8000`, then open `http://localhost:8000`.
2. The page shows the receipts bar, count chips (CLEAR / REVIEW / FLAGGED / NO_COVERAGE), and the client table, all matching M1's CLI output at strictness 0.5.
3. Drag the Dial from 0 to 1.
   - The CLEAR count falls and REVIEW rises.
   - FLAGGED stays unchanged.
   - `jev_calls` in the receipts bar does not change.
4. Click a collision client. The drawer shows its articles as CLEAR with "different person" and the Jev fields.
5. `curl "localhost:8000/api/results?strictness=1.0"` returns JSON with `clients`, `counts`, and `receipts`.

**Files created/modified**

- `app/api.py`
- `static/index.html`
- `tests/test_api.py`

**Task list**

| ID | Name | Responsibility | Depends on |
|---|---|---|---|
| M2.1 | API endpoints + static serving | `GET /api/results?strictness=` (re-routes the stored `ScreenRun` via the router, no Jev calls), `GET /api/client/{id}`, `POST /api/screen`, and the static mount for `index.html`. | M1.4, M1.6 |
| M2.2 | Dashboard page | `index.html` with the receipts bar, debounced Dial slider calling `/api/results`, count chips, and client table with status chips. | M2.1 |
| M2.3 | Client drawer | Row click opens a drawer from `/api/client/{id}` listing each article's verdict, reason, Jev fields, and title-only badge. | M2.2 |

**Cut line**

1. Make `POST /api/screen` reload-only (no live re-screen) first.
2. Next, reduce the drawer to title, verdict, and reason.
3. Drop CSS polish.

Never cut: the Dial re-routing from stored decisions.

---

### M3: Eval + Memo  [SHOULD]  [1:55 → 2:10, 15 min]

**What exists at the end**

`scripts/eval.py` compares Jev-routed verdicts against hand labels and a keyword baseline, printing precision, recall, and false positives for each; these are the pitch numbers. Flagged and review clients get an on-demand "Generate memo" button. It calls the LLM to write an analyst memo of 6 lines or fewer from the client's HIT/REVIEW articles, citing URLs, and caches the memo in memory.

**Integration demo**

1. `python scripts/eval.py` prints a table `method | precision | recall | false_positives` with rows for `Jev` and `keyword_baseline`, over about 20–30 labeled pairs.
2. In the UI, click a FLAGGED client, then "Generate memo." A memo of 6 lines or fewer appears, with article URLs.
3. Clicking again returns the cached memo instantly.

**Files created/modified**

- `scripts/eval.py`
- `data/labels.csv`
- `app/memo.py`
- `app/api.py`
- `static/index.html`
- `tests/test_eval.py`
- `tests/test_memo.py`

**Task list**

| ID | Name | Responsibility | Depends on |
|---|---|---|---|
| M3.1 | Eval script | Loads `labels.csv` and `results.json`, computes Jev HIT-vs-label and keyword-baseline metrics, and prints the comparison table. `labels.csv` is hand-labeled by the human in parallel during M2. | M1.4, M1.6 |
| M3.2 | Memo service + endpoint | `memo.py` builds the prompt from HIT/REVIEW articles and calls the OpenAI-compatible LLM; `POST /api/memo/{id}` caches results in memory. | M2.1 |
| M3.3 | Memo button | "Generate memo" button in the drawer that calls `/api/memo/{id}` and renders the memo with links. | M2.3, M3.2 |

**Cut line**

Cut the memo first (M3.3, then M3.2). Keep M3.1; the eval numbers carry the pitch.

---

### M4: Monitor Mode  [BONUS]  [2:05 → 2:15, start only if M1–M3 are done by 2:05]

**What exists at the end**

`POST /api/monitor/tick` fetches GDELT articles published since the last tick, screens only the new ones, merges them into the stored `ScreenRun`, and returns new alerts. The UI has a "Run monitor tick" button and an alert list.

**Integration demo**

1. Click "Run monitor tick."
2. The alert list shows any new HIT/REVIEW articles (or "no new alerts").
3. Receipts show only the new Jev calls added.

**Files created/modified**

- `app/api.py`
- `app/gdelt.py`
- `app/screening.py`
- `static/index.html`
- `tests/test_monitor.py`

**Task list**

| ID | Name | Responsibility | Depends on |
|---|---|---|---|
| M4.1 | Monitor tick | Fetches new articles since the last tick, screens only those, merges them into the `ScreenRun`, and returns alerts; adds the UI button and alert list. | M1.6, M2.3 |

**Cut line**

Skip entirely if not started by 2:05. Mention monitor mode verbally in the pitch as a roadmap item.
