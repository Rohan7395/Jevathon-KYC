# tasks-M3.md — KYC Sentinel, M3: Eval + Memo

---

### Task M3.1: Eval script

Goal: Compare Jev-routed article verdicts and a naive keyword baseline against hand labels. Print precision, recall, and false positives for each; these numbers are the pitch.

File(s):
- `scripts/eval.py` (new)
- `tests/test_eval.py` (new)
- `data/labels.csv` is written by the human, not the coder. Do not create or overwrite it.

Contract:

Signature(s):
```python
# scripts/eval.py
from pathlib import Path
from app.models import Article, ArticleDecision, Client, ScreenRun, Thresholds, Verdict
from app.router import verdict_for_article

KEYWORDS: list[str] = ["fraud", "arrest", "charged", "bribe", "laundering",
                       "sanction", "convicted", "probe", "raid"]

def load_labels(path: Path) -> list[tuple[str, str, int]]: ...
    # returns [(client_id, article_id, is_hit), ...]
def load_run(path: Path) -> ScreenRun: ...
def last_name(client: Client) -> str: ...
def keyword_hit(article: Article, client: Client) -> bool: ...
def jev_hit(decision: ArticleDecision | None, t: Thresholds) -> bool: ...
def compute_metrics(predictions: list[bool], truths: list[bool]) -> dict[str, float | int]: ...
    # keys: "precision", "recall", "false_positives", "tp", "fn", "n"
def evaluate(run: ScreenRun, labels: list[tuple[str, str, int]]
             ) -> tuple[dict[str, dict[str, float | int]], int]: ...
    # returns ({"Jev": metrics, "keyword_baseline": metrics}, skipped_count)
def format_table(results: dict[str, dict[str, float | int]]) -> str: ...
def main(labels_path: Path = Path("data/labels.csv"),
         results_path: Path = Path("data/results.json")) -> int: ...
    # returns the exit code
if __name__ == "__main__":
    raise SystemExit(main())
```

Inputs / Outputs (from `app/models.py`, already defined; do not redefine):
- `ScreenRun`: `clients: list[Client]`, `articles: list[Article]`, `decisions: list[ArticleDecision]`, `receipts: Receipts`. Load it with `ScreenRun.model_validate_json(path.read_text())`.
- `Client`: `id: str`, `name: str`, plus optional fields.
- `Article`: `id: str` (unique, includes client), `client_id: str`, `url: str`, `title: str`, `text: str | None`, `title_only: bool`.
- `ArticleDecision`: `article_id`, `client_id`, `same_person: float | None`, `is_subject: float | None`, `risk_type: str | None`, `risk_conf: float | None`, `severity: int | None`, `error: str | None`, plus other fields.
- `verdict_for_article(d: ArticleDecision, t: Thresholds) -> tuple[Verdict, str]` returns `(verdict, reason)`.
- `labels.csv` format. RESOLVED: header `client_id,article_id,is_hit`, where `is_hit` is `1` or `0`. Extra columns (e.g. `note`) are ignored.

Semantics:
- `jev_hit`: `True` only if `decision is not None` and `verdict_for_article(decision, Thresholds())[0] == Verdict.HIT`. REVIEW counts as not-hit. RESOLVED: eval uses default `Thresholds()` (strictness 0.5); HIT-side thresholds are fixed by the Dial anyway.
- `last_name`: the last whitespace-separated token of `client.name`, lowercased. RESOLVED.
- `keyword_hit`: `True` iff `last_name(client)` is a substring of `article.title.lower()` AND any keyword in `KEYWORDS` is a substring of `(article.title + " " + (article.text or "")).lower()`. RESOLVED: the keyword search covers title + text, so the baseline sees the same input Jev saw. Matching is plain substring with no word boundaries; the baseline is naive by design.
- `compute_metrics`:
  - `tp` = pred ∧ truth; `fp` = pred ∧ ¬truth; `fn` = ¬pred ∧ truth.
  - `precision` = tp/(tp+fp), or `0.0` if the denominator is 0.
  - `recall` = tp/(tp+fn), or `0.0` if the denominator is 0.
  - `false_positives` = fp; `n` = len(truths).
  - Raise `ValueError` if the two lists differ in length.
- `evaluate`:
  - Index `run.articles` by `id`, `run.clients` by `id`, and `run.decisions` by `article_id`.
  - For each label, skip it (increment `skipped`, log WARNING `"skip label {client_id},{article_id}: <why>"`) if any of these hold:
    - `article_id` is not in the articles index
    - `client_id` is not in the clients index
    - `article.client_id != client_id`
  - A labeled article with no decision is kept; `jev_hit(None, ...)` is `False` (fail-closed means not-HIT).
  - Both methods are scored on exactly the same kept pairs.
- `format_table`: exact header `method | precision | recall | false_positives`. Print one row each for `Jev` then `keyword_baseline`, with precision and recall to 2 decimals, e.g. `Jev | 0.92 | 0.85 | 1`.
- `main`: prints the table, then `n = {n} labeled pairs (skipped {skipped})`, and returns `0`.

Errors:
- `labels.csv` missing: print `error: labels file not found: {path}` to stderr and return `1`.
- `results.json` missing: print `error: results not found: {path} (run scripts/run_screen.py first)` to stderr and return `1`.
- A row with `is_hit` not in `{"0","1"}` (after strip): log WARNING, skip the row, and do not count it in `n`.
- Zero usable labels: print `error: no usable labels` to stderr and return `1`.
- Never call Jev, the LLM, or any network.

Depends on:
- M1.4: `app.router.verdict_for_article`
- M1.6: `data/results.json` in `ScreenRun` shape
- M1.1: `app.models.{ScreenRun, Client, Article, ArticleDecision, Thresholds, Verdict}`

Implementation constraints:
- Put `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` at the top of `scripts/eval.py`, before `app` imports, so `python scripts/eval.py` works from the repo root.
- Use stdlib `csv` only. No new dependencies.
- Router HIT rule, for building test fixtures (plan §8, verbatim; defaults `same_person_hit=0.70`, `is_subject_hit=0.60`, `hit_risk_conf=0.70`, `same_person_clear=0.25`):
  ```
  if d.error or any of (same_person, is_subject, risk_type, risk_conf, severity) is None → REVIEW
  if d.same_person < t.same_person_clear → CLEAR, "different person"
  if d.same_person >= t.same_person_hit and d.is_subject >= t.is_subject_hit
     and d.risk_type != "none" and d.risk_conf >= t.hit_risk_conf → HIT
  ```

Acceptance tests (`tests/test_eval.py`):
1. `compute_metrics([T,T,T,F,F], [T,T,F,T,F])` returns precision ≈ 0.667, recall ≈ 0.667, false_positives == 1, tp == 2, fn == 1, n == 5. Also, `compute_metrics([F,F],[F,F])` returns precision == 0.0 and recall == 0.0 without raising.
2. `jev_hit`:
   - Decision `same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2` returns `True`.
   - The same decision with `error="timeout"` returns `False`.
   - `jev_hit(None, Thresholds())` returns `False`.
3. `keyword_hit` with client `"Michael Jordan"`:
   - Title `"Jordan charged in fraud scheme"` returns `True`.
   - Title `"Jordan wins award"` with `text=None` returns `False`.
   - Title `"Smith charged"` returns `False`.
4. `evaluate` on an in-test `ScreenRun` with one collision client `"Michael Jordan"` and article title `"Michael Jordan charged with fraud"`:
   - Decision is `same_person=0.05, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2`; label is `is_hit=0`.
   - Expect `results["Jev"]["false_positives"] == 0` and `results["keyword_baseline"]["false_positives"] == 1`.
   - A second label with an unknown `article_id` makes `skipped == 1`.
5. `main(tmp_labels, tmp_results)` with files written to `tmp_path` returns `0`, and `capsys` output contains `method | precision | recall | false_positives`, `Jev |`, and `keyword_baseline |`. Also, `main(tmp_path/"missing.csv", tmp_results)` returns `1`.

Done when: `pytest -q tests/test_eval.py` passes, and `python scripts/eval.py` against the real `data/labels.csv` + `data/results.json` prints the two-row table.

---

### Task M3.2: Memo service + endpoint

Goal: `app/memo.py` builds a prompt from one client's HIT/REVIEW articles and calls the OpenAI-compatible LLM to write an analyst memo of 6 lines or fewer that cites URLs. `POST /api/memo/{client_id}` serves it and caches it in memory.

File(s):
- `app/memo.py` (new)
- `app/api.py` (modify)
- `tests/test_memo.py` (new)

Contract:

Signature(s):
```python
# app/memo.py
from openai import OpenAI
from app.models import Article, ArticleDecision, Client, Verdict

MAX_MEMO_LINES: int = 6
MAX_MEMO_ARTICLES: int = 10

class MemoError(Exception): ...

MemoItem = tuple[Article, ArticleDecision | None, Verdict, str]   # (article, decision, verdict, reason)

def select_memo_articles(
    articles: list[Article],
    decisions: dict[str, ArticleDecision],          # article_id → decision
    verdicts: dict[str, tuple[Verdict, str]],       # article_id → (verdict, reason)
    limit: int = MAX_MEMO_ARTICLES,
) -> list[MemoItem]: ...

def build_memo_prompt(client: Client, items: list[MemoItem]) -> list[dict[str, str]]: ...
    # OpenAI chat messages

def enforce_line_limit(text: str, max_lines: int = MAX_MEMO_LINES) -> str: ...

def generate_memo(
    client: Client,
    articles: list[Article],
    decisions: dict[str, ArticleDecision],
    verdicts: dict[str, tuple[Verdict, str]],
    llm: OpenAI | None = None,
    model: str | None = None,
) -> str: ...
```
```python
# app/api.py (additions)
from app.memo import MemoError, generate_memo

_memo_cache: dict[str, str] = {}          # client_id → memo

def get_run() -> ScreenRun: ...           # see RESOLVED below

@app.post("/api/memo/{client_id}")
def memo_endpoint(client_id: str) -> dict: ...
    # → {"client_id": str, "memo": str, "cached": bool}
```

Inputs / Outputs:
- `Client`: `id`, `name`, `birth_year`, `city`, `country`, `occupation`, `organization` (all but `id`/`name` may be None).
- `Article`: `id`, `client_id`, `url`, `title`, `domain`, `seen_date`, `title_only: bool`.
- `ArticleDecision`: `same_person`, `is_subject`, `risk_type`, `risk_conf`, `severity` (0..3), and `error`. Any of these may be None.
- `Verdict`: `CLEAR | REVIEW | HIT`.
- `Status`: `CLEAR | REVIEW | FLAGGED | NO_COVERAGE`.
- `ClientResult.status: Status`.
- Router (M1.4):
  - `verdict_for_article(d: ArticleDecision, t: Thresholds) -> tuple[Verdict, str]`
  - `status_for_client(client_id: str, verdicts: dict[str, tuple[Verdict, str]], decisions: dict[str, ArticleDecision], t: Thresholds) -> ClientResult`
- Severity labels (index → text): `0 "Rumor or unverified allegation"`, `1 "Formal investigation, raid, or lawsuit"`, `2 "Criminally charged or indicted"`, `3 "Convicted, sanctioned, or penalized"`.

Semantics, `app/memo.py`:
- `select_memo_articles`:
  - Keep only articles whose verdict is HIT or REVIEW. An article missing from `verdicts` is treated as `(Verdict.REVIEW, "fail-closed: no decision")`.
  - Order: HIT before REVIEW; within each group, by severity desc (None as -1), then same_person desc (None as -1).
  - Truncate to `limit`.
- `build_memo_prompt`:
  - The system message says:
    - You write a compliance analyst memo of at most 6 lines.
    - Use only the facts provided.
    - Cite the article URL for every claim.
    - Do not assign a status, decision, or recommendation to clear or flag.
    - Plain text, no markdown headers.
  - The user message contains the client identity fields (non-None only), then one block per item with: title, url, domain, seen_date, verdict, reason, same_person, is_subject, risk_type, severity + label, and `title_only`.
  - Format None values as `n/a`, and format floats to 2 decimals.
  - Articles with `decision.error` set are marked `"Jev error — not assessed"`.
- `enforce_line_limit`: strip, split on newlines, drop blank lines, keep the first `max_lines`, and join with `"\n"`.
- `generate_memo`:
  1. `items = select_memo_articles(...)`. If there are none, raise `MemoError("no_hit_or_review_articles")`.
  2. If `llm is None`, `import app.config` (which loads `.env`) and read `os.getenv("LLM_BASE_URL")`, `os.getenv("LLM_API_KEY")`, `os.getenv("LLM_MODEL")`. If any is missing, raise `MemoError("llm_not_configured: missing <NAME>")`. Otherwise create `OpenAI(base_url=..., api_key=...)`. When `model is None`, use `LLM_MODEL`.
  3. Call `llm.chat.completions.create(model=model, messages=..., temperature=0.2, max_tokens=400, timeout=30)`.
  4. Wrap any exception as `MemoError(f"llm_call_failed: {type(e).__name__}: {e}")`.
  5. Read `resp.choices[0].message.content`. If it is None or empty after strip, raise `MemoError("empty_llm_response")`.
  6. Return `enforce_line_limit(content)`. RESOLVED: the ≤6-line limit is enforced in code, not trusted to the LLM.

Semantics, `app/api.py`:
- RESOLVED, `get_run()`: if M2.1 already has a function that returns the stored `ScreenRun`, make `get_run` an alias for it. Otherwise add:
  ```python
  def get_run() -> ScreenRun:
      return ScreenRun.model_validate_json(Path("data/results.json").read_text())
  ```
  The memo endpoint must call `get_run()` by that name so tests can monkeypatch `app.api.get_run`.
- RESOLVED: the memo uses default `Thresholds()` (strictness 0.5), independent of the Dial. The cache is keyed by `client_id` only.
- `memo_endpoint(client_id)`:
  1. If `client_id` is in `_memo_cache`, return `{"client_id", "memo": cached, "cached": True}` without calling the LLM.
  2. `run = get_run()`. Find the client; if absent, return 404 with `{"detail": "unknown client"}`.
  3. Gather the client's articles (`a.client_id == client_id`) and `decisions = {d.article_id: d for d in run.decisions if d.client_id == client_id}`.
  4. Build `verdicts = {a.id: verdict_for_article(decisions[a.id], Thresholds()) if a.id in decisions else (Verdict.REVIEW, "fail-closed: no decision")}`.
  5. Compute the status with `status_for_client(client_id, verdicts, decisions, Thresholds())`. If it is not in `{FLAGGED, REVIEW}`, return 409 with `{"detail": "memo only for FLAGGED/REVIEW clients (status: <STATUS>)"}`.
  6. Call `generate_memo(client, articles, decisions, verdicts)`.
     - On `MemoError`, log a WARNING and return 502 with `{"detail": str(e)}`. Do not cache.
     - On success, store the memo in `_memo_cache[client_id]` and return `{"client_id", "memo", "cached": False}`.
- In the existing `POST /api/screen` handler, add `_memo_cache.clear()` after a successful run or reload. RESOLVED: this keeps memos from going stale.

Errors:
- `memo.py` raises only `MemoError`; it never lets other exceptions escape `generate_memo`.
- The endpoint returns 404 / 409 / 502 as above and never 500 for LLM problems.
- No Jev calls anywhere in this task.

Depends on:
- M1.1: `app.models.{Client, Article, ArticleDecision, Verdict, Status, Thresholds, ScreenRun}`
- M1.4: `app.router.{verdict_for_article, status_for_client}`
- M2.1: the FastAPI `app` object in `app/api.py`, the existing stored-run accessor, and the `POST /api/screen` handler

Implementation constraints:
- `openai` is already in requirements. No new dependencies.
- In tests, mock the LLM with a fake object exposing `.chat.completions.create(**kw)`, e.g. built with `types.SimpleNamespace`. Never make real network calls.

Acceptance tests (`tests/test_memo.py`):
1. `select_memo_articles` with 4 articles:
   - Inputs: A CLEAR; B REVIEW with severity None; C HIT with severity 1, same_person 0.8; D HIT with severity 3, same_person 0.9.
   - Expect ids `[D, C, B]`. With `limit=2`, expect `[D, C]`.
2. `generate_memo` with a fake LLM whose content is 8 non-blank lines plus blank lines returns exactly 6 lines. The captured `messages` contain every selected article's `url` and do not contain the CLEAR article's url.
3. `generate_memo` where the fake LLM's `create` raises `RuntimeError("boom")` raises `MemoError` whose message starts with `"llm_call_failed"`. A fake returning `content=None` raises `MemoError("empty_llm_response")`.
4. API via `fastapi.testclient.TestClient(app)`, with `app.api.get_run` monkeypatched to return an in-test `ScreenRun` and `app.api.generate_memo` monkeypatched to a counting stub returning `"line1\nline2"`. Clear `_memo_cache` first.
   - A FLAGGED client (HIT decision `same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2`): the first POST returns 200 with `cached False`, the second returns 200 with `cached True`, and the stub is called once.
5. Same setup as test 4:
   - A client whose only decision has `same_person=0.05` (CLEAR) returns 409.
   - `POST /api/memo/nope` returns 404.
   - A stub raising `MemoError("x")` returns 502 with `detail == "x"`.

Done when: `pytest -q tests/test_memo.py` passes, and `curl -X POST localhost:8000/api/memo/<flagged_id>` returns a memo of 6 lines or fewer with URLs; a second curl returns `"cached": true` instantly.

---

### Task M3.3: Memo button

Goal: Add a "Generate memo" button to the existing client drawer. It calls `POST /api/memo/{id}` and renders the memo with clickable URLs.

File(s):
- `static/index.html` (modify)
- `tests/test_static_memo.py` (new)

Contract:

Signature(s), vanilla JS inside `index.html`, no build step, no libraries:
```js
function renderMemoControls(clientId, status)   // called when the drawer opens for a client
async function generateMemo(clientId)           // click handler
function escapeHtml(s) -> string
function linkify(escapedText) -> string         // wraps URLs in <a>
```

Inputs / Outputs:
- Endpoint (from M3.2): `POST /api/memo/{client_id}`.
  - 200 returns `{"client_id": str, "memo": str, "cached": bool}`; the memo is at most 6 newline-separated lines.
  - 404, 409, and 502 return `{"detail": str}`.
- Status values: `CLEAR | REVIEW | FLAGGED | NO_COVERAGE`.

Required DOM, inside the existing drawer from M2.3:
- `<button id="memo-btn">Generate memo</button>`
- `<div id="memo-output"></div>`

Behavior:
- RESOLVED: button visibility uses the clicked row's `status` from the most recent `/api/results` response. Show `#memo-btn` only when the status is `FLAGGED` or `REVIEW`; otherwise hide the button and clear `#memo-output`.
- Opening the drawer for a different client clears `#memo-output` and resets the button.
- On click:
  1. Disable the button and set its text to `Generating…`.
  2. `fetch("/api/memo/" + encodeURIComponent(clientId), {method: "POST"})`.
  3. On 200, render each memo line as its own `<div>` with `innerHTML = linkify(escapeHtml(line))`. If `cached` is true, append a small `<span class="memo-cached">(cached)</span>`.
  4. On non-200, render `Memo failed: {detail}` in `#memo-output` with a red style (use `textContent`). A network exception is handled the same way with its message.
  5. Always re-enable the button and restore its text.
- Ignore stale responses: if the drawer switched clients before the response arrived, drop it.
- `escapeHtml` escapes `& < > " '`. RESOLVED: LLM output is untrusted and is always escaped before linkify.
- `linkify` replaces `/https?:\/\/[^\s<)\]]+/g` with `<a href="$&" target="_blank" rel="noopener noreferrer">$&</a>`.

Errors: no uncaught promise rejections; every failure path shows a message in `#memo-output`. Do not change existing drawer, Dial, table, or receipts behavior.

Depends on:
- M2.3: the existing drawer and row-click handler in `static/index.html`
- M3.2: `POST /api/memo/{client_id}` and its response shape

Implementation constraints:
- Vanilla JS and minimal CSS only. No new files, libraries, or CDN imports.
- No new API endpoints.

Acceptance tests (`tests/test_static_memo.py`):
1. `TestClient(app).get("/")` returns 200 and the HTML contains `id="memo-btn"`, `id="memo-output"`, and `/api/memo/`.
2. The HTML contains `function escapeHtml` and `function linkify`, and contains `rel="noopener` (link safety present).

Done when: `pytest -q tests/test_static_memo.py` passes. Manually, in the browser at `localhost:8000`:
- Click a FLAGGED client, then Generate memo. The memo appears with clickable links.
- Click again. It shows `(cached)` instantly.
- A CLEAR client shows no button.