# KYC Sentinel — Build Plan (for Cursor)

> **For the planning model (Opus):** This is the source of truth. Decompose each milestone into tasks using the task template at the bottom. **Do not invent APIs.** Jev is newer than your training data; use only the Jev reference in §5.
>
> **Hard time limit:** code freeze at **2:15 PM**, demo at 2:30. It is ~12:45 now.

---

## 1. Product brief

**Problem.** Banks and fintechs must screen customers for adverse media (fraud, corruption, sanctions, etc.) under AML/KYC rules. Today analysts search each name and manually clear hundreds of irrelevant hits: people with the same name, victims, passing mentions. It's slow, costly, and real risk gets buried.

**User.** An AML/compliance analyst (Priya), plus her compliance manager, who sets the risk appetite.

**What the product does:**
1. Loads a client portfolio.
2. Pulls news about each client from GDELT.
3. Uses **Jev** to judge every (client, article) pair: same person? the perpetrator? what risk? how severe?
4. **Code** routes each client to CLEAR / REVIEW / FLAGGED / NO_COVERAGE. It **fails closed**: uncertainty or error means REVIEW, never CLEAR.
5. The manager moves an **Autonomy Dial** (strictness) and sees the workload shift live.
6. An LLM writes an analyst memo **only for flagged/review clients, on demand**.
7. Every decision is written to an **audit log**.
8. A **receipts meter** shows calls, tokens, Jev $, time, and an estimate of what an LLM would have cost.

**Out of scope today:**
- auth
- a real database (JSON files only)
- non-English handling beyond what Jev does
- continuous monitoring (bonus only, M4)
- deployment
- sanctions-list matching

---

## 2. Architecture

```
portfolio.csv ─► gdelt.py (fetch + cache) ─► news_cache.json
                                                  │
                                                  ▼
                 screening.py (async orchestration, semaphore)
                         │ per (client, article)
                         ▼
                 jev_client.py ──HTTP──► Jev /v1/systemone
                         │ ArticleDecision (raw probs, never thresholded here)
                         ▼
                 router.py (PURE functions: thresholds → verdicts → client status)
                         │
            ┌────────────┼──────────────┬───────────────┐
            ▼            ▼              ▼               ▼
        audit.py     receipts.py    results.json     memo.py ──► LLM (OpenAI-compatible)
        (JSONL)                          │
                                         ▼
                               api.py (FastAPI) ◄── static/index.html (vanilla JS)
```

**Design rules (these are the pitch too):**
- **Jev judges, code decides, the LLM only writes.** Thresholds live in code, never in prompts.
- **Store raw Jev probabilities** in `ScreenRun` (`results.json`). The Dial re-routes from stored results server-side, with **zero new Jev calls**, so it's instant.
- **The router is pure and deterministic.** It's the most unit-tested module.
- **Fail closed.** Any Jev error, timeout, or missing field puts that article in REVIEW.
- **One Jev call per (client, article)**, run concurrently (semaphore 8). This keeps state small and accurate, per Jev's documented weakness with large state.

---

## 3. Stack & layout

- **Language/libraries:** Python 3.11, `fastapi`, `uvicorn`, `httpx`, `pydantic` v2, `openai` (for the OpenAI-compatible memo LLM), `pytest`, `pytest-asyncio`, and optionally `trafilatura`.
- **Frontend:** one `static/index.html` with vanilla JS and minimal CSS. No build step.
- **Config** comes from env vars (`.env` via `python-dotenv`):
  - `JEV_BASE_URL` — `https://api.typesafe.ai` **or** `https://openrouter.ai/api`
  - `JEV_API_KEY`, `JEV_MODEL` (default `jev-1.13`)
  - `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`

```
kyc-sentinel/
  app/
    config.py        # env + default thresholds + price constants
    models.py        # ALL shared pydantic models (§4). Nothing else defines models.
    gdelt.py         # fetch articles per client, cache to JSON
    jev_client.py    # async Jev call for one (client, article) → ArticleDecision
    screening.py     # orchestrates fan-out, collects decisions, writes results/audit/receipts
    router.py        # pure: verdict_for_article(), status_for_client(), thresholds_from_strictness()
    audit.py         # append-only JSONL writer
    receipts.py      # counters + cost math
    memo.py          # LLM memo for one client
    api.py           # FastAPI app + static serving
  scripts/
    fetch_news.py    # CLI: portfolio → news_cache.json
    run_screen.py    # CLI: cache → results.json (+ prints summary)
    eval.py          # CLI: labels.csv → Jev vs keyword-baseline metrics
  data/
    portfolio.csv  news_cache.json  results.json  audit.jsonl  labels.csv
  static/index.html
  tests/
```

---

## 4. Shared data models (`app/models.py`, defined ONCE)

```python
from enum import Enum
from pydantic import BaseModel

class Client(BaseModel):
    id: str
    name: str
    birth_year: int | None = None
    city: str | None = None
    country: str | None = None
    occupation: str | None = None
    organization: str | None = None

class Article(BaseModel):
    id: str                 # sha1(client_id + url)[:12]  — globally unique (same URL can belong to 2 clients)
    client_id: str
    url: str
    title: str
    domain: str | None = None
    seen_date: str | None = None
    language: str | None = None
    text: str | None = None # trimmed ≤ 1500 chars, or None
    title_only: bool        # True if text is None

RISK_TYPES = ["fraud", "corruption_bribery", "money_laundering",
              "sanctions_terror", "violent_crime", "regulatory_action", "none"]

class ArticleDecision(BaseModel):
    article_id: str
    client_id: str
    same_person: float | None = None   # Noul P(yes)
    is_subject: float | None = None    # Noul P(yes)
    risk_type: str | None = None       # Choice
    risk_conf: float | None = None
    risk_probs: dict[str, float] = {}
    severity: int | None = None        # Score index 0..3
    severity_conf: float | None = None
    error: str | None = None           # set → router must return REVIEW
    latency_ms: int = 0
    input_tokens: int = 0
    model: str | None = None

class Verdict(str, Enum):
    CLEAR = "CLEAR"; REVIEW = "REVIEW"; HIT = "HIT"

class Status(str, Enum):
    CLEAR = "CLEAR"; REVIEW = "REVIEW"; FLAGGED = "FLAGGED"; NO_COVERAGE = "NO_COVERAGE"

class Thresholds(BaseModel):
    # CLEAR-side (moved by the Dial). Defaults == thresholds_from_strictness(0.5)
    same_person_clear: float = 0.25   # same_person <  → different person → CLEAR
    not_subject_clear: float = 0.20   # is_subject  <  (and same person) → victim/bystander → CLEAR
    none_clear_conf: float = 0.725    # risk_type=="none" with confidence ≥ → CLEAR
    # HIT-side (FIXED, not moved by the Dial)
    same_person_hit: float = 0.70
    is_subject_hit: float = 0.60
    hit_risk_conf: float = 0.70
    flag_same_person: float = 0.85    # HIT → FLAGGED only if same_person ≥ this AND severity ≥ 1

class ClientResult(BaseModel):
    client_id: str
    status: Status
    reasons: list[str]
    article_verdicts: dict[str, Verdict]   # article_id → verdict
    worst_article_id: str | None = None

class Receipts(BaseModel):
    jev_calls: int = 0
    jev_errors: int = 0
    input_tokens: int = 0
    jev_cost_usd: float = 0.0
    elapsed_s: float = 0.0
    llm_equiv_cost_usd: float = 0.0   # labeled "estimate" in UI

class ScreenRun(BaseModel):
    """The ONLY shape written to / read from data/results.json. Verdicts/statuses are NOT stored — always recomputed by router."""
    clients: list[Client]
    articles: list[Article]
    decisions: list[ArticleDecision]
    receipts: Receipts
```

**Keying rule:** all maps are keyed by `article_id`, which is unique because it includes `client_id`.

---

## 5. Jev reference (the ONLY source of truth for Jev)

**Endpoint:** `POST {JEV_BASE_URL}/v1/systemone`, with headers `Authorization: Bearer {JEV_API_KEY}` and `Content-Type: application/json`.

- This works for both TypeSafe and OpenRouter; the base URL differs.
- **Use `httpx` directly.** Don't depend on the SDK.

**Request (per client × article):**
```json
{
  "model": "jev-1.13",
  "state": {
    "client": {"name": "...", "birth_year": 1971, "city": "...", "country": "...", "occupation": "...", "organization": "..."},
    "article": {"title": "...", "text": "... or null", "date": "...", "source": "domain"}
  },
  "questions": {
    "same_person": {"type": "noul",
      "instructions": "The article is about the same individual described in state.client (not just someone with the same name)."},
    "is_subject": {"type": "noul",
      "instructions": "In the article, this individual is accused of, investigated for, charged with, or convicted of wrongdoing (not a victim, witness, commentator, or bystander)."},
    "risk_type": {"type": "choice",
      "instructions": "The main type of wrongdoing the article attributes to this individual.",
      "criteria": {
        "fraud": "Financial fraud, scams, embezzlement, tax evasion",
        "corruption_bribery": "Bribery, kickbacks, political corruption",
        "money_laundering": "Money laundering or hiding illicit funds",
        "sanctions_terror": "Sanctions violations or terrorism financing",
        "violent_crime": "Violent or organized crime",
        "regulatory_action": "Fines, bans, or enforcement by a regulator",
        "none": "No wrongdoing attributed to this individual"}},
    "severity": {"type": "score",
      "instructions": "How far the legal process has gone for this individual.",
      "criteria": ["Rumor or unverified allegation", "Formal investigation, raid, or lawsuit", "Criminally charged or indicted", "Convicted, sanctioned, or penalized"]}
  }
}
```

**Response shape** (as documented):
```json
{
  "model": "jev-1.13.0",
  "answers": {
    "same_person": {"type": "noul", "noul": 0.91},
    "is_subject":  {"type": "noul", "noul": 0.88},
    "risk_type":   {"type": "choice", "choice": "fraud", "confidence": 0.8, "probabilities": {"fraud": 0.9, "none": 0.02}},
    "severity":    {"type": "score", "score": 1.0, "confidence": 0.8, "probabilities": {"0": 0.1, "1": 0.8}}
  },
  "usage": {"input_tokens": 392, "output_tokens": 65}
}
```

**Response → `ArticleDecision` mapping (exact):**
- `same_person` = `answers.same_person.noul`
- `is_subject` = `answers.is_subject.noul`
- `risk_type` = `answers.risk_type.choice`
- `risk_conf` = `answers.risk_type.confidence`, **not** `probabilities[choice]`
- `risk_probs` = `answers.risk_type.probabilities`
- `severity` = `int(round(answers.severity.score))`. The score is a **level index** (0..3) returned as a float.
- `severity_conf` = `answers.severity.confidence`
- `input_tokens` = `usage.input_tokens`
- `model` = the top-level `model` field

**If any of these fields is missing or None,** set `error="missing_field:<name>"`; that counts as a Jev error. **Log the full raw JSON of the first response** at INFO level, so shape mismatches are visible immediately.

**Jev rules:**
- **Noul returns only `noul`** (P(yes)), with no confidence field.
- **Limits:** 32k tokens per state+question and 64k for everything. Keep article text ≤ 1500 chars.
- **Price:** `$42 per 1B input tokens` (= `4.2e-8` per token); output is free.
- **Jev can't do math, dates, or counting.** Do those in code.
- **Timeout 10s. Retry once on 429/5xx with backoff.** After that, return an `ArticleDecision` with `error` set. **Never raise out of `jev_client`.**

---

## 6. GDELT reference

**Request:**
`GET https://api.gdeltproject.org/api/v2/doc/doc?query="{NAME}"&mode=artlist&format=json&maxrecords=25&sort=datedesc`

- Returns `{"articles":[{"url","title","seendate","domain","language","sourcecountry"}...]}`. There's no body text.
- **GDELT returns HTTP 200 with plain text on rate-limit or a bad query.** Detect it with `if not r.text.lstrip().startswith("{")`. Log a warning with `r.text[:120]`, wait 10s, retry once. If it's still non-JSON, record the client in `fetch_failures` and print them at the end. **Never silently treat it as zero articles.**
- **Throttle to ~1 request per 5 seconds.** Fetch **once**, cache to `data/news_cache.json`, and screen from the cache. **The demo never depends on live GDELT.**
- **Optional:** fetch body text for the top 5 articles per client with `trafilatura` (5s timeout). On failure, set `title_only=True`.
- **Code prefilter:** keep an article only if the client's last name appears in the title or text (case-insensitive).

---

## 7. Demo data (`data/portfolio.csv`, 25–40 rows)

Build it in three groups:
- **Documented public cases (~5).** Well-known *convicted or formally charged* individuals with lots of news coverage. **Only documented cases.**
- **Name-collision clients (~5).** A famous name with a *different* fictional identity, e.g. "Michael Jordan, b.1982, Columbus OH, dentist." Coverage of the famous person should be **CLEARED** as a different person. This is the star moment of the demo.
- **Common-name clean clients (~15+).** E.g. "John Smith, b.1980, Denver, software engineer." Lots of irrelevant hits should end up auto-cleared.

**`labels.csv`** (for eval): hand-label ~20–30 (client_id, article_id) pairs from the cache: `is_hit` = 1/0. Include collisions and victim/bystander articles.

---

## 8. Router logic (`app/router.py`, pure, fully unit-tested)

```
verdict_for_article(d: ArticleDecision, t: Thresholds) -> tuple[Verdict, str]   # (verdict, reason)
  if d.error or any of (same_person, is_subject, risk_type, risk_conf, severity) is None
      → REVIEW, "fail-closed: <error or missing field>"
  if d.same_person < t.same_person_clear                      → CLEAR, "different person"
  if d.same_person >= t.same_person_hit and d.is_subject < t.not_subject_clear
                                                             → CLEAR, "not the subject (victim/bystander)"
  if d.risk_type == "none" and d.risk_conf >= t.none_clear_conf → CLEAR, "no wrongdoing"
  if d.same_person >= t.same_person_hit and d.is_subject >= t.is_subject_hit
     and d.risk_type != "none" and d.risk_conf >= t.hit_risk_conf → HIT, "<risk_type>, severity <n>"
  else                                                        → REVIEW, "uncertain"

status_for_client(
    client_id: str,
    verdicts: dict[str, tuple[Verdict, str]],    # article_id → (verdict, reason)
    decisions: dict[str, ArticleDecision],        # article_id → decision
    t: Thresholds,
) -> ClientResult
  no articles → NO_COVERAGE
  any HIT with same_person >= t.flag_same_person AND severity >= 1 → FLAGGED
  any HIT or any REVIEW → REVIEW
  else → CLEAR
  worst_article_id = HIT with max (severity, same_person), else first REVIEW, else None
  reasons = distinct reason strings, worst first
  (ClientResult.article_verdicts stores only the Verdict per article_id)

thresholds_from_strictness(s: float) -> Thresholds          # s clamped to [0,1]
  Moves ONLY the CLEAR-side fields, so the Dial is monotonic
  (strict = fewer auto-CLEARs = more REVIEW; HITs never change):
    same_person_clear: 0.40 (s=0) → 0.10 (s=1)
    not_subject_clear: 0.30 (s=0) → 0.10 (s=1)
    none_clear_conf:   0.55 (s=0) → 0.90 (s=1)
  Linear interpolation. s=0.5 MUST equal the Thresholds() defaults (test this).
```

**Severity use:** a "rumor" (0) HIT is REVIEW, not FLAGGED. Severity shows in reasons and the UI.

**Health check** in `run_screen.py`, after screening:
`assert receipts.jev_errors < 0.2 * max(receipts.jev_calls, 1), f"{receipts.jev_errors}/{receipts.jev_calls} Jev failures"`
This catches a silent all-REVIEW portfolio caused by a parsing bug.

---

## 9. Milestones

### M1 — Screening core, CLI only (12:45 → 1:25) · **MUST**
**Built:**
- config
- models
- GDELT fetch + cache
- Jev client (fail-closed)
- pure router
- screening orchestrator
- audit log
- receipts
- two CLI scripts

**Integration demo:**
1. `python scripts/fetch_news.py` creates `news_cache.json`.
2. `python scripts/run_screen.py` prints a table of `client | status | reason` plus a receipts line, writes `results.json`, and appends to `audit.jsonl`.

**Tests:** the router is exhaustively covered (every branch, including error → REVIEW). The Jev client is tested with a mocked httpx: success, 429→retry, timeout → `error` set.

### M2 — API + UI (1:25 → 1:55) · **MUST**
**Built:**
- FastAPI with:
  - `GET /api/results?strictness=0.5` → `{clients: [...ClientResult + Client], counts, receipts}`, re-routed from stored decisions with **no Jev calls**
  - `GET /api/client/{id}` → client + articles + decisions + verdicts
  - `POST /api/screen` → runs screening (or reloads cache)
- `index.html`:
  - receipts bar at the top
  - Dial slider (debounced, calls `/api/results`)
  - count chips
  - client table with status chips
  - click a row → a drawer listing that client's articles with verdict, same_person, is_subject, risk_type, severity, and a title-only badge

**Integration demo:** open `localhost:8000`, see the portfolio statuses, drag the Dial and watch the counts change, open the collision client and see its articles CLEAR with "different person."

### M3 — Memo + Eval (1:55 → 2:15) · **SHOULD** (cut the memo first if late)
**Built:**
- `POST /api/memo/{id}` → the LLM writes a ≤6-line analyst memo from the client's HIT/REVIEW articles (title, url, Jev answers), citing URLs, cached in memory. There's a "Generate memo" button in the drawer.
- `scripts/eval.py` runs Jev decisions vs `labels.csv` and compares against a **keyword baseline** (last name in title AND any of ["fraud","arrest","charged","bribe","laundering","sanction","convicted","probe","raid"] → hit). It prints precision, recall, and false positives for each. Show these numbers in the pitch.

**Integration demo:** click a flagged client → Generate memo → the memo appears with links. `python scripts/eval.py` prints the comparison table.

### M4 — Monitor mode (bonus, only if done by 2:05)
A `POST /api/monitor/tick` fetches new GDELT articles for all clients since the last tick, screens only the new ones, and returns new alerts. The UI gets a "Run monitor tick" button and an alert list.

### 2:15 → 2:30 — Freeze
Rehearse the 2-minute script, commit, submit on HackerSquad. **No new code.**

---

## 10. Rules for AI coders (paste into every coding chat)
- Import models only from `app/models.py`. Never redefine a model.
- Follow the task contract exactly: signatures, return types, error behavior.
- No features beyond the task. No new dependencies unless the task names them.
- `jev_client` never raises. Errors go into `ArticleDecision.error`.
- The router is pure: no I/O, no globals.
- Every task ships with pytest tests. External HTTP is always mocked in tests.

---

## 11. Task template (Opus: use this for every task)

```
### Task M{m}.{n}: {name}
Goal: 1–2 sentences.
File(s): exact paths.
Contract:
  Signature(s): exact Python signatures with types.
  Inputs / Outputs: reference models from §4.
  Errors: exact behavior on failure.
  Depends on: task IDs + the contract names used.
Acceptance tests: 2–5 concrete pytest cases (input → expected).
Done when: tests pass + one-line manual check.
```

**Decomposition limits (time!):**
- ≤ 6 tasks for M1
- ≤ 4 for M2
- ≤ 3 for M3
- 1 for M4

**Each task should take ~5–8 minutes to code.** Merge tiny tasks; don't split for purity.
