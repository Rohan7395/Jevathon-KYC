# tasks-M1.md — KYC Sentinel · M1: Screening Core (CLI only)

## Global rules (paste with every task)
- Python 3.11. Import models only from `app/models.py`. Never redefine or modify a model. Plain helper classes (e.g. exceptions) are fine; no new pydantic models outside `models.py`.
- Follow the contract exactly: signatures, return types, and error behavior.
- No features beyond the task. No new dependencies beyond `requirements.txt` (M1.1).
- `jev_client` never raises. Errors go into `ArticleDecision.error`.
- `router.py` is pure: no I/O, no globals, no logging.
- Every task ships with pytest tests. External HTTP is always mocked with `httpx.MockTransport` (no `respx`, no real network).
- `pytest.ini` sets `asyncio_mode = auto` and `pythonpath = .`, so async tests need no marker and `import app...` works from the repo root.

---

### Task M1.1: Config + Models
Goal: Create the package skeleton, env/config loading with all constants, and `app/models.py` exactly as specified. Every other task imports from these two files.
File(s): `app/__init__.py` (empty), `app/config.py`, `app/models.py`, `requirements.txt`, `.env.example`, `pytest.ini`, `tests/__init__.py` (empty), `tests/test_config.py`
Contract:
  Signature(s):
  ```python
  # app/config.py
  from dataclasses import dataclass
  from pathlib import Path

  ROOT_DIR: Path          # = Path(__file__).resolve().parents[1]
  DATA_DIR: Path          # = ROOT_DIR / "data"
  PORTFOLIO_CSV: Path     # = DATA_DIR / "portfolio.csv"
  NEWS_CACHE_JSON: Path   # = DATA_DIR / "news_cache.json"
  RESULTS_JSON: Path      # = DATA_DIR / "results.json"
  AUDIT_JSONL: Path       # = DATA_DIR / "audit.jsonl"

  DEFAULT_STRICTNESS: float = 0.5
  JEV_DEFAULT_MODEL: str = "jev-1.13"
  JEV_TIMEOUT_S: float = 10.0
  JEV_RETRY_BACKOFF_S: float = 1.0
  JEV_CONCURRENCY: int = 8
  ARTICLE_TEXT_MAX_CHARS: int = 1500

  GDELT_URL: str = "https://api.gdeltproject.org/api/v2/doc/doc"
  GDELT_MAXRECORDS: int = 25
  GDELT_THROTTLE_S: float = 5.0
  GDELT_NONJSON_RETRY_WAIT_S: float = 10.0
  GDELT_HTTP_TIMEOUT_S: float = 20.0

  JEV_PRICE_PER_INPUT_TOKEN: float = 4.2e-8          # $42 per 1B input tokens; output is free
  LLM_EQUIV_INPUT_PRICE_PER_TOKEN: float = 3e-6       # $3 / 1M input tokens
  LLM_EQUIV_OUTPUT_PRICE_PER_TOKEN: float = 1.5e-5    # $15 / 1M output tokens
  LLM_EQUIV_OUTPUT_TOKENS_PER_CALL: int = 150
  HEALTH_MAX_ERROR_RATIO: float = 0.2

  @dataclass(frozen=True)
  class Settings:
      jev_base_url: str
      jev_api_key: str
      jev_model: str
      llm_base_url: str | None
      llm_api_key: str | None
      llm_model: str | None

  def load_settings(require_jev: bool = True) -> Settings: ...
  ```
  `app/models.py` — copy EXACTLY (this is the whole file):
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
  Inputs / Outputs: env vars `JEV_BASE_URL`, `JEV_API_KEY`, `JEV_MODEL` (default `"jev-1.13"`), `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. `load_settings` calls `dotenv.load_dotenv()` (does not override already-set env vars), strips any trailing `/` from `jev_base_url`, and maps empty strings to `None` for the LLM fields.
  Errors: if `require_jev` is True and `JEV_BASE_URL` or `JEV_API_KEY` is missing or empty → `raise RuntimeError("Missing env var: <NAME>")`. With `require_jev=False`, missing Jev vars become `""`. LLM vars are optional in M1.
  Depends on: —
Implementation constraints:
  - `requirements.txt`:
    ```
    fastapi
    uvicorn
    httpx>=0.27
    pydantic>=2
    python-dotenv
    openai
    pytest
    pytest-asyncio>=0.23
    ```
    RESOLVED: `trafilatura` is omitted. The body-text fetch is optional in the plan and first on the cut line, so all M1 articles are `title_only=True`.
  - `.env.example`:
    ```
    # https://api.typesafe.ai  OR  https://openrouter.ai/api
    JEV_BASE_URL=https://api.typesafe.ai
    JEV_API_KEY=
    JEV_MODEL=jev-1.13
    LLM_BASE_URL=
    LLM_API_KEY=
    LLM_MODEL=
    ```
  - `pytest.ini`:
    ```
    [pytest]
    pythonpath = .
    asyncio_mode = auto
    testpaths = tests
    ```
  - RESOLVED: the plan gives no LLM-equivalent price, so the four `LLM_EQUIV_*` constants above are a generic frontier-LLM assumption, used only for the estimate.
Acceptance tests (`tests/test_config.py`):
  1. `Thresholds()` → `same_person_clear == 0.25`, `not_subject_clear == 0.20`, `none_clear_conf == 0.725`, `flag_same_person == 0.85`.
  2. `ArticleDecision(article_id="a", client_id="c")` → all judgment fields are `None`, `risk_probs == {}`, `error is None`, `latency_ms == 0`.
  3. `monkeypatch.delenv("JEV_API_KEY", raising=False)`, `monkeypatch.setenv("JEV_BASE_URL", "https://x/")`, and monkeypatch `app.config.load_dotenv` to a no-op → `load_settings()` raises `RuntimeError` whose message contains `"JEV_API_KEY"`.
  4. Set `JEV_BASE_URL="https://api.typesafe.ai/"`, `JEV_API_KEY="k"`, unset `JEV_MODEL`, `LLM_MODEL=""` → `jev_base_url == "https://api.typesafe.ai"`, `jev_model == "jev-1.13"`, `llm_model is None`.
Done when: `pytest -q tests/test_config.py` passes, and `python -c "import app.models, app.config"` runs clean.

---

### Task M1.2: GDELT fetch + cache + `fetch_news.py` + portfolio
Goal: Load the portfolio, fetch each client's news from GDELT once (throttled, fail-loud on non-JSON), prefilter by last name, and cache to `data/news_cache.json`. The demo never depends on live GDELT.
File(s): `app/gdelt.py`, `scripts/fetch_news.py`, `data/portfolio.csv`, `tests/test_gdelt.py`
Contract:
  Signature(s):
  ```python
  # app/gdelt.py
  import time
  from collections.abc import Callable
  from pathlib import Path
  import httpx
  from app.models import Client, Article

  class GdeltFetchError(Exception): ...

  def load_portfolio(path: Path) -> list[Client]: ...
  def make_article_id(client_id: str, url: str) -> str: ...
  def last_name(name: str) -> str: ...
  def mentions_last_name(client: Client, title: str, text: str | None) -> bool: ...
  def parse_articles(client: Client, payload: dict) -> list[Article]: ...
  def fetch_client_articles(client: Client, http: httpx.Client, *,
                            sleep: Callable[[float], None] = time.sleep) -> list[Article]: ...
  def fetch_all(clients: list[Client], http: httpx.Client, *,
                throttle_s: float = 5.0,
                sleep: Callable[[float], None] = time.sleep,
                on_progress: Callable[[int, int, Client, int | None], None] | None = None,
                ) -> tuple[dict[str, list[Article]], list[str]]: ...
  def save_cache(path: Path, articles_by_client: dict[str, list[Article]], fetch_failures: list[str]) -> None: ...
  def load_cache(path: Path) -> tuple[dict[str, list[Article]], list[str]]: ...
  ```
  Inputs / Outputs:
  - `Client` fields: `id, name, birth_year: int|None, city, country, occupation, organization` (all optional except `id`, `name`).
  - `Article` fields: `id, client_id, url, title, domain, seen_date, language, text, title_only`.
  - `load_portfolio`: reads a CSV with header `id,name,birth_year,city,country,occupation,organization`. Empty cells become `None`; `birth_year` is converted to `int`.
  - `make_article_id` = `hashlib.sha1((client_id + url).encode("utf-8")).hexdigest()[:12]`. RESOLVED: plain concatenation, no separator.
  - `last_name(name)` = the last whitespace-separated token of `name` (e.g. `"Sam Bankman-Fried"` → `"Bankman-Fried"`).
  - `mentions_last_name`: RESOLVED: a whole-word, case-insensitive match, `re.search(rf"\b{re.escape(last)}\b", s, re.I)`, against the title and against `text` if it isn't None. True if either matches.
  - `parse_articles`: iterates `payload.get("articles", [])`. It skips items missing `url` or `title` and maps `url→url`, `title→title`, `domain→domain`, `seendate→seen_date` (raw string, unparsed), `language→language`. It sets `text=None`, `title_only=True`, `id=make_article_id(client.id, url)`, `client_id=client.id`. It keeps only items where `mentions_last_name` is True, and dedupes by `url` (keeping the first). Order is preserved. `payload == {}` returns `[]`; that is a valid zero-result response, not a failure.
  - `fetch_client_articles`: one `GET GDELT_URL` with `params={"query": f'"{client.name}"', "mode": "artlist", "format": "json", "maxrecords": "25", "sort": "datedesc"}` and `timeout=20.0`.
  - `fetch_all`: processes clients in order. It calls `sleep(throttle_s)` before every request except the first (about 1 request every 5s). A client that raises `GdeltFetchError` is appended to `fetch_failures` and left out of `articles_by_client`. A successful client is always present, even with `[]`. It calls `on_progress(i, total, client, n_articles_or_None_on_failure)` after each client, with `i` 1-based.
  - Cache file JSON: `{"articles_by_client": {client_id: [Article.model_dump(), ...]}, "fetch_failures": [client_id, ...]}`. `save_cache` creates parent dirs. `load_cache` returns the same tuple, with Article objects rebuilt via `Article.model_validate`.
  Errors:
  - A response is "bad" if any of these hold: `httpx.HTTPError` raised, status ≠ 200, `not r.text.lstrip().startswith("{")`, or `json.loads` fails.
  - On a bad response: `logger.warning("GDELT non-JSON for %s: %r", client.name, r.text[:120])` (for exceptions, log the exception instead of the text). Then `sleep(10.0)` and retry once.
  - If still bad → `raise GdeltFetchError(client.id)`.
  - Never treat a bad response as zero articles.
  Depends on: M1.1 — `Client`, `Article` from `app.models`; `GDELT_URL`, `GDELT_MAXRECORDS` (25), `GDELT_THROTTLE_S` (5.0), `GDELT_NONJSON_RETRY_WAIT_S` (10.0), `GDELT_HTTP_TIMEOUT_S` (20.0), `PORTFOLIO_CSV`, `NEWS_CACHE_JSON` from `app.config`.
Implementation constraints:
  - GDELT (copied from the plan): it returns `{"articles":[{"url","title","seendate","domain","language","sourcecountry"}...]}` with no body text. **GDELT returns HTTP 200 with plain text on rate-limit or a bad query.** Detect this with `if not r.text.lstrip().startswith("{")`, log a warning with `r.text[:120]`, wait 10s, and retry once. If it's still non-JSON, record the client in `fetch_failures` and print them at the end. **Never silently treat it as zero articles.**
  - `scripts/fetch_news.py`:
    - First line after imports: `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`.
    - Configure `logging.basicConfig(level=logging.INFO)` and `logging.getLogger("httpx").setLevel(logging.WARNING)`.
    - Load `PORTFOLIO_CSV`, then run `fetch_all` inside `with httpx.Client() as http:`.
    - Print a progress line per client: `[i/N] <name>: <k> articles` or `[i/N] <name>: FETCH FAILED`.
    - `save_cache(NEWS_CACHE_JSON, ...)`, then print `Wrote data/news_cache.json (<total> articles, <n> clients)`.
    - If there are failures, print `fetch_failures: <comma-separated client ids>`.
  - `data/portfolio.csv` (exactly these 26 rows: 6 documented cases, 5 name collisions, 15 common names):
    ```
    id,name,birth_year,city,country,occupation,organization
    C001,Sam Bankman-Fried,1992,,US,crypto exchange founder,FTX
    C002,Elizabeth Holmes,1984,,US,biotech founder,Theranos
    C003,Martin Shkreli,1983,,US,pharmaceutical executive,Turing Pharmaceuticals
    C004,Do Kwon,1991,,South Korea,crypto founder,Terraform Labs
    C005,Charlie Javice,1992,,US,fintech founder,Frank
    C006,Carlos Ghosn,1954,,Lebanon,automotive executive,Nissan
    C007,Michael Jordan,1982,Columbus,US,dentist,Riverside Dental
    C008,Taylor Swift,1975,Tulsa,US,accountant,Prairie Tax Services
    C009,Michael Cohen,1990,Austin,US,high school teacher,Austin ISD
    C010,Tom Brady,1968,Sacramento,US,plumber,Brady Plumbing
    C011,Kevin Hart,1985,Omaha,US,insurance agent,Heartland Mutual
    C012,John Smith,1980,Denver,US,software engineer,Acme Cloud
    C013,Maria Garcia,1977,Phoenix,US,nurse,Valley General Hospital
    C014,James Johnson,1969,Atlanta,US,logistics manager,Peach Freight
    C015,David Lee,1988,Seattle,US,data analyst,Northwind Analytics
    C016,Michael Brown,1983,Cleveland,US,electrician,Lakeshore Electric
    C017,Jennifer Wilson,1991,Raleigh,US,marketing manager,Oakleaf Media
    C018,Robert Miller,1959,Milwaukee,US,retired teacher,
    C019,Wei Zhang,1986,Toronto,Canada,civil engineer,Maple Bridgeworks
    C020,Ahmed Khan,1979,Manchester,UK,pharmacist,Northgate Pharmacy
    C021,Sarah Davis,1994,Boston,US,graphic designer,Harbor Studio
    C022,Daniel Nguyen,1990,San Jose,US,mechanical engineer,Bayline Robotics
    C023,Emily Clark,1987,Portland,US,veterinarian,Cedar Animal Clinic
    C024,Carlos Rodriguez,1975,Miami,US,restaurant owner,Casa Rodriguez
    C025,Linda Martinez,1964,San Antonio,US,school administrator,Alamo Unified
    C026,Anna Kowalski,1993,Chicago,US,paralegal,Lakeview Legal
    ```
Acceptance tests (`tests/test_gdelt.py`, `httpx.Client(transport=httpx.MockTransport(handler))`, `sleep` replaced by a recorder):
  1. **(Must keep; cut-line survivor)** The handler returns `200` with text `"Please limit requests to one every 5 seconds..."` on both calls → `fetch_client_articles` raises `GdeltFetchError`. The handler was called 2×, the recorder saw `sleep(10.0)`, and `caplog` contains `"Please limit requests"`. Via `fetch_all([client], ...)` → returns `({}, ["C001"])`.
  2. Non-JSON first, then `{"articles":[{"url":"https://a/1","title":"Bankman-Fried trial update","domain":"a","seendate":"20250101T000000Z","language":"English"}]}` → 1 Article with `id == hashlib.sha1(b"C001https://a/1").hexdigest()[:12]`, `title_only is True`, `text is None`, and `seen_date == "20250101T000000Z"`.
  3. `parse_articles(client "Sam Bankman-Fried", {"articles":[t1 "BANKMAN-FRIED sentenced", t2 "Crypto markets fall", dup of t1 url]})` → exactly 1 article (the case-insensitive match is kept, the non-match dropped, and the duplicate URL deduped). `parse_articles(c, {}) == []`.
  4. `fetch_all` with 3 clients and a valid JSON handler → `sleep` called exactly 2× with `5.0`, and all 3 client ids present in the returned dict.
  5. `load_portfolio` on a tmp CSV with an empty `birth_year` → `birth_year is None`. A `save_cache` then `load_cache` round trip returns equal Articles and failures.
Done when: tests pass, and `python scripts/fetch_news.py` prints throttled per-client lines and writes `data/news_cache.json`.

---

### Task M1.3: Jev client
Goal: Make an async single call to Jev for one (client, article) pair and map the response exactly to `ArticleDecision`. It never raises; every failure becomes `ArticleDecision.error`.
File(s): `app/jev_client.py`, `tests/test_jev_client.py`
Contract:
  Signature(s):
  ```python
  import asyncio
  from collections.abc import Awaitable, Callable
  import httpx
  from app.models import Client, Article, ArticleDecision

  QUESTIONS: dict  # module constant, exactly the "questions" object below

  def build_request_body(client: Client, article: Article, model: str) -> dict: ...
  def parse_response(article: Article, payload: dict) -> ArticleDecision: ...
  async def judge_article(
      client: Client,
      article: Article,
      http: httpx.AsyncClient,
      *,
      base_url: str,
      api_key: str,
      model: str = "jev-1.13",
      timeout_s: float = 10.0,
      backoff_s: float = 1.0,
      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
  ) -> ArticleDecision: ...
  ```
  Inputs / Outputs:
  - `Article` fields used: `id, client_id, title, text, seen_date, domain`.
  - `Client` fields used: `name, birth_year, city, country, occupation, organization`.
  - The returned `ArticleDecision` always has `article_id=article.id` and `client_id=article.client_id`.
  - `latency_ms` = the int wall-clock ms of the whole call, including any retry.
  - The HTTP call is `POST {base_url.rstrip('/')}/v1/systemone` with headers `Authorization: Bearer {api_key}` and `Content-Type: application/json`, and `timeout=timeout_s`.
  Errors (never raise; always return an `ArticleDecision`):
  - `429` or `5xx` → `await sleep(backoff_s)` and retry once. If still `429`/`5xx` → `error="http_<status>"`.
  - Any other non-200 → `error="http_<status>"`, no retry.
  - `httpx.TimeoutException` → `error="timeout"`. RESOLVED: timeouts are not retried; only 429/5xx get the one retry. This bounds the worst-case latency.
  - Other `httpx.HTTPError` → `error="network:<ExceptionClassName>"`.
  - A body that isn't valid JSON, or JSON that isn't a dict → `error="bad_json"`.
  - A required field missing or None → `error="missing_field:<name>"`. Check in this order: `same_person, is_subject, risk_type, risk_conf, severity, severity_conf`, and report the first missing one. Fields that parsed successfully, plus `input_tokens`/`model`, are still filled in.
  - RESOLVED: `risk_probs`, `usage.input_tokens`, and `model` are not required. If missing, they default to `{}`, `0`, and `None`, with `logger.warning`. The router needs only the six fields above, and treating these as errors would fail every call on a provider that omits `usage`.
  - Any other unexpected exception → `error="exception:<ExceptionClassName>"`.
  Depends on: M1.1 — `Client`, `Article`, `ArticleDecision` from `app.models`.
Implementation constraints (copied from the plan; exact):
  - Request body:
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
  - Body mapping:
    - `"model"` = the `model` argument.
    - `state.client` = the six client fields (None → null).
    - `state.article.title` = `article.title`.
    - `state.article.text` = `article.text[:1500]`, or null.
    - `state.article.date` = `article.seen_date`.
    - `state.article.source` = `article.domain`.
  - Documented response:
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
  - Mapping (exact):
    - `same_person` = `answers.same_person.noul`
    - `is_subject` = `answers.is_subject.noul`
    - `risk_type` = `answers.risk_type.choice`
    - `risk_conf` = `answers.risk_type.confidence` (**not** `probabilities[choice]`)
    - `risk_probs` = `answers.risk_type.probabilities`
    - `severity` = `int(round(answers.severity.score))` (the score is a level index 0..3 returned as a float)
    - `severity_conf` = `answers.severity.confidence`
    - `input_tokens` = `usage.input_tokens`
    - `model` = the top-level `model`
  - Noul returns only `noul` (P(yes)), with no confidence field.
  - **Log the full raw JSON of the first response at INFO level.** Keep a module-level flag `_first_logged = False`. On the first HTTP response received (any status), call `logger.info("First Jev response (HTTP %s): %s", status, r.text)` and set the flag.
  - Use `httpx` directly; no Jev SDK.
Acceptance tests (`tests/test_jev_client.py`, `httpx.AsyncClient(transport=httpx.MockTransport(handler))`, `sleep` = an async recorder):
  1. Success: the handler returns the documented response → `same_person==0.91`, `is_subject==0.88`, `risk_type=="fraud"`, `risk_conf==0.8`, `risk_probs=={"fraud":0.9,"none":0.02}`, `severity==1`, `severity_conf==0.8`, `input_tokens==392`, `model=="jev-1.13.0"`, `error is None`. The captured request URL ends with `/v1/systemone`, the `Authorization` header is `"Bearer k"`, and the body has `state.article.title` and all 4 question keys.
  2. 429 then 200 → success decision. The handler was called 2× and `sleep` was awaited once with `backoff_s`.
  3. 503 twice → `error=="http_503"`, the handler called exactly 2×, and no exception.
  4. The handler raises `httpx.ReadTimeout("t")` → `error=="timeout"`, and no exception.
  5. The documented response with `answers.is_subject` removed → `error=="missing_field:is_subject"`, `same_person==0.91`. Also: with `monkeypatch.setattr(jev_client, "_first_logged", False)`, `caplog` at INFO contains `"First Jev response"`.
Done when: tests pass, and one real call made from a REPL with `.env` keys returns a decision with `error is None`.

---

### Task M1.4: Router
Goal: Implement pure, deterministic functions that turn raw `ArticleDecision`s plus `Thresholds` into per-article verdicts and per-client statuses, plus the strictness→thresholds mapping for the Autonomy Dial. This is the most heavily tested module.
File(s): `app/router.py`, `tests/test_router.py`
Contract:
  Signature(s):
  ```python
  from app.models import ArticleDecision, ClientResult, Status, Thresholds, Verdict

  def verdict_for_article(d: ArticleDecision, t: Thresholds) -> tuple[Verdict, str]: ...
  def status_for_client(
      client_id: str,
      verdicts: dict[str, tuple[Verdict, str]],    # article_id → (verdict, reason)
      decisions: dict[str, ArticleDecision],        # article_id → decision
      t: Thresholds,
  ) -> ClientResult: ...
  def thresholds_from_strictness(s: float) -> Thresholds: ...
  ```
  Inputs / Outputs:
  - `Thresholds` defaults: `same_person_clear=0.25`, `not_subject_clear=0.20`, `none_clear_conf=0.725`, `same_person_hit=0.70`, `is_subject_hit=0.60`, `hit_risk_conf=0.70`, `flag_same_person=0.85`.
  - `ClientResult`: `client_id`, `status: Status`, `reasons: list[str]`, `article_verdicts: dict[str, Verdict]` (Verdict only, no reason), `worst_article_id: str | None`.
  Errors: none raised for any `ArticleDecision` input. Missing data yields REVIEW (fail-closed).
  Depends on: M1.1 — `ArticleDecision`, `ClientResult`, `Status`, `Thresholds`, `Verdict` from `app.models`.
Implementation constraints (copied from the plan §8; evaluate branches in this exact order):
  ```
  verdict_for_article(d, t):
    if d.error or any of (same_person, is_subject, risk_type, risk_conf, severity) is None
        → REVIEW, "fail-closed: <error or missing field>"
    if d.same_person < t.same_person_clear                      → CLEAR, "different person"
    if d.same_person >= t.same_person_hit and d.is_subject < t.not_subject_clear
                                                               → CLEAR, "not the subject (victim/bystander)"
    if d.risk_type == "none" and d.risk_conf >= t.none_clear_conf → CLEAR, "no wrongdoing"
    if d.same_person >= t.same_person_hit and d.is_subject >= t.is_subject_hit
       and d.risk_type != "none" and d.risk_conf >= t.hit_risk_conf → HIT, "<risk_type>, severity <n>"
    else                                                        → REVIEW, "uncertain"

  status_for_client(...):
    no articles → NO_COVERAGE
    any HIT with same_person >= t.flag_same_person AND severity >= 1 → FLAGGED
    any HIT or any REVIEW → REVIEW
    else → CLEAR
    worst_article_id = HIT with max (severity, same_person), else first REVIEW, else None
    reasons = distinct reason strings, worst first

  thresholds_from_strictness(s)   # s clamped to [0,1]
    Moves ONLY the CLEAR-side fields (strict = fewer auto-CLEARs = more REVIEW; HITs never change):
      same_person_clear: 0.40 (s=0) → 0.10 (s=1)
      not_subject_clear: 0.30 (s=0) → 0.10 (s=1)
      none_clear_conf:   0.55 (s=0) → 0.90 (s=1)
    Linear interpolation. s=0.5 MUST equal the Thresholds() defaults.
  ```
  - Severity semantics: a "rumor" (severity 0) HIT is REVIEW, not FLAGGED.
  - RESOLVED (fail-closed reason text): if `d.error` is set → `f"fail-closed: {d.error}"`. Otherwise use the first None among `same_person, is_subject, risk_type, risk_conf, severity`, in that order → `f"fail-closed: missing_field:{name}"`.
  - RESOLVED (HIT reason text): `f"{d.risk_type}, severity {d.severity}"`, e.g. `"fraud, severity 2"`.
  - RESOLVED (NO_COVERAGE): the condition is `verdicts` being empty. The result is `reasons=["no articles"]`, `article_verdicts={}`, `worst_article_id=None`.
  - RESOLVED (worst article): among HIT ids, `max(key=(decisions[id].severity, decisions[id].same_person))`; ties go to the first in `verdicts` insertion order. If there's no HIT, use the first REVIEW id in `verdicts` insertion order.
  - RESOLVED (reason order): the worst article's reason comes first if one exists. Then the remaining distinct reasons, grouped HIT, then REVIEW, then CLEAR, each group in `verdicts` insertion order. No duplicates.
  - RESOLVED (float exactness): `thresholds_from_strictness` computes `lo + (hi - lo) * s` and wraps each value in `round(x, 6)`, so `thresholds_from_strictness(0.5) == Thresholds()` holds exactly. The HIT-side fields keep their `Thresholds()` defaults.
  - Pure: no I/O, no logging, no module-level mutable state.
Acceptance tests (`tests/test_router.py`; helper `dec(**kw)` defaults to `same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2, severity_conf=0.8`; `T=Thresholds()`):
  1. Verdict branches:
     - `dec(error="timeout")` → `(REVIEW, "fail-closed: timeout")`
     - `dec(severity=None)` → `(REVIEW, "fail-closed: missing_field:severity")`
     - `dec(same_person=0.1)` → `(CLEAR, "different person")`
     - `dec(is_subject=0.1)` → `(CLEAR, "not the subject (victim/bystander)")`
     - `dec(risk_type="none", risk_conf=0.8)` → `(CLEAR, "no wrongdoing")`
     - `dec()` → `(HIT, "fraud, severity 2")`
     - `dec(same_person=0.5)` → `(REVIEW, "uncertain")`
     - Boundary: `dec(same_person=0.25)` is not "different person"
  2. Status rules:
     - `{}` → NO_COVERAGE
     - one HIT with `same_person=0.9, severity=2` → FLAGGED, `worst_article_id` is that id, `reasons[0]=="fraud, severity 2"`
     - HIT with `severity=0` → REVIEW
     - HIT with `same_person=0.8, severity=3` → REVIEW (below 0.85)
  3. Status aggregation:
     - three CLEAR "different person" → CLEAR, `reasons==["different person"]`, `worst_article_id is None`
     - `{a1: CLEAR, a2: REVIEW "uncertain"}` → REVIEW, `worst_article_id=="a2"`, `reasons==["uncertain","different person"]`
     - `article_verdicts` maps ids → Verdict only
  4. `thresholds_from_strictness(0.5) == Thresholds()`. `s=0` gives `(0.40, 0.30, 0.55)`; `s=1` gives `(0.10, 0.10, 0.90)`. `s=-1` equals `s=0` and `s=2` equals `s=1`. The HIT-side fields are identical across all `s`.
  5. Monotonic Dial: a fixed list of ~10 varied decisions, routed at `s = 0.0, 0.1, …, 1.0` → the CLEAR count is non-increasing and the HIT count is constant. Also `dec(risk_type="none", risk_conf=0.6)` → CLEAR at `s=0` and REVIEW at `s=0.5`.
Done when: `pytest -q tests/test_router.py` passes with every branch covered.

---

### Task M1.5: Audit + Receipts
Goal: Provide an append-only JSONL audit writer (one line per decision) and receipts math (counters, Jev $, LLM-equivalent estimate), plus the formatted receipts line and the health check.
File(s): `app/audit.py`, `app/receipts.py`, `tests/test_audit_receipts.py`
Contract:
  Signature(s):
  ```python
  # app/audit.py
  from pathlib import Path
  from app.models import ArticleDecision, Verdict

  def append_decisions(
      path: Path,
      run_id: str,
      decisions: list[ArticleDecision],
      verdicts: dict[str, tuple[Verdict, str]],   # article_id → (verdict, reason)
      strictness: float,
  ) -> int: ...   # returns number of lines appended

  # app/receipts.py
  from app.models import ArticleDecision, Receipts

  def build_receipts(decisions: list[ArticleDecision], elapsed_s: float) -> Receipts: ...
  def format_receipts(r: Receipts) -> str: ...
  def assert_healthy(r: Receipts) -> None: ...
  ```
  Inputs / Outputs:
  - Audit line (one JSON object per decision, `\n`-terminated, file opened in append mode `"a"`, parent dirs created):
    `{"ts": <UTC ISO-8601>, "run_id": str, "client_id": str, "article_id": str, "verdict": "CLEAR"|"REVIEW"|"HIT", "reason": str, "strictness": float, "decision": d.model_dump()}`
  - RESOLVED: if `verdicts` lacks an `article_id`, write `verdict="REVIEW"`, `reason="fail-closed: no verdict"`.
  - `Receipts` fields: `jev_calls, jev_errors, input_tokens, jev_cost_usd, elapsed_s, llm_equiv_cost_usd`.
  - `build_receipts`:
    - `jev_calls = len(decisions)` (RESOLVED: one per decision; retries not counted separately)
    - `jev_errors` = the count with `error is not None`
    - `input_tokens` = the sum of `d.input_tokens`
    - `jev_cost_usd = input_tokens * JEV_PRICE_PER_INPUT_TOKEN`
    - `llm_equiv_cost_usd = input_tokens * LLM_EQUIV_INPUT_PRICE_PER_TOKEN + jev_calls * LLM_EQUIV_OUTPUT_TOKENS_PER_CALL * LLM_EQUIV_OUTPUT_PRICE_PER_TOKEN`
    - `elapsed_s` as given
  - `format_receipts` → exactly `f"Jev calls: {n} | errors: {e} | tokens: {t} | Jev $: {jev:.6f} | elapsed: {el:.1f}s | LLM est $: {llm:.4f}"`.
  Errors:
  - `append_decisions` lets I/O errors propagate (the audit must not silently fail).
  - `assert_healthy` executes exactly `assert r.jev_errors < 0.2 * max(r.jev_calls, 1), f"{r.jev_errors}/{r.jev_calls} Jev failures"` (raises `AssertionError`).
  Depends on: M1.1 — `ArticleDecision`, `Receipts`, `Verdict` from `app.models`. From `app.config`, import these constants (don't hardcode them):
  - `JEV_PRICE_PER_INPUT_TOKEN = 4.2e-8`
  - `LLM_EQUIV_INPUT_PRICE_PER_TOKEN = 3e-6`
  - `LLM_EQUIV_OUTPUT_PRICE_PER_TOKEN = 1.5e-5`
  - `LLM_EQUIV_OUTPUT_TOKENS_PER_CALL = 150`
Acceptance tests (`tests/test_audit_receipts.py`):
  1. `append_decisions(tmp/"a.jsonl", "run1", [d1, d2], {d1.article_id: (Verdict.HIT, "fraud, severity 2")}, 0.5)` called twice → the file has 4 lines, each `json.loads` with all 8 keys. `d2`'s line has `verdict=="REVIEW"` and `reason=="fail-closed: no verdict"`. The return value is 2.
  2. `build_receipts([d(input_tokens=1000), d(input_tokens=500, error="timeout")], 3.21)` → `jev_calls==2`, `jev_errors==1`, `input_tokens==1500`, `jev_cost_usd≈6.3e-5`, `llm_equiv_cost_usd≈0.009` (use `pytest.approx`).
  3. `format_receipts` of that result == `"Jev calls: 2 | errors: 1 | tokens: 1500 | Jev $: 0.000063 | elapsed: 3.2s | LLM est $: 0.0090"`.
  4. `assert_healthy(Receipts(jev_calls=10, jev_errors=2))` raises `AssertionError` with message `"2/10 Jev failures"`. `jev_errors=1` passes, and `Receipts()` (0/0) passes.
Done when: tests pass.

---

### Task M1.6: Screening orchestrator + `run_screen.py`
Goal: Load the cache, fan out one Jev call per (client, article) with a semaphore of 8, and build the `ScreenRun`. Then route, audit, print the status table and receipts, run the health check, and write `results.json`.
File(s): `app/screening.py`, `scripts/run_screen.py`, `tests/test_screening.py`
Contract:
  Signature(s):
  ```python
  # app/screening.py
  from collections.abc import Awaitable, Callable
  from pathlib import Path
  from app.models import Article, ArticleDecision, Client, ClientResult, ScreenRun, Thresholds, Verdict

  JudgeFn = Callable[[Client, Article], Awaitable[ArticleDecision]]

  async def screen(
      clients: list[Client],
      articles_by_client: dict[str, list[Article]],
      judge: JudgeFn,
      *,
      concurrency: int = 8,
  ) -> ScreenRun: ...
  def verdicts_for_run(run: ScreenRun, t: Thresholds) -> dict[str, tuple[Verdict, str]]: ...
  def route_run(run: ScreenRun, t: Thresholds) -> list[ClientResult]: ...
  def save_run(path: Path, run: ScreenRun) -> None: ...
  def load_run(path: Path) -> ScreenRun: ...
  ```
  Inputs / Outputs:
  - `ScreenRun = {clients: list[Client], articles: list[Article], decisions: list[ArticleDecision], receipts: Receipts}`. Verdicts and statuses are NOT stored; they are always recomputed by the router.
  - `screen`:
    - `clients` = all portfolio clients, in order. A client with no articles (or absent from the dict) stays in `clients` and later routes to NO_COVERAGE.
    - `articles` = flattened in client order, then article order.
    - `decisions` = one per article, in the same order (via `asyncio.gather` over tasks, each guarded by `asyncio.Semaphore(concurrency)`).
    - `receipts = build_receipts(decisions, elapsed_s)`, where `elapsed_s` = `time.perf_counter()` around the fan-out.
  - `verdicts_for_run` → `{article.id: verdict_for_article(decision, t)}` for every article in `run.articles`.
  - `route_run` → one `ClientResult` per client in `run.clients` order, via `status_for_client(client.id, verdicts_of_that_client_in_article_order, decisions_of_that_client, t)`.
  - `save_run` writes `run.model_dump_json(indent=2)` and creates parent dirs. `load_run` = `ScreenRun.model_validate_json(path.read_text())`.
  Errors:
  - If `judge` raises (it shouldn't), `screen` catches it and records `ArticleDecision(article_id=a.id, client_id=a.client_id, error=f"exception:{type(e).__name__}")`. `screen` never raises because of one article.
  - RESOLVED: in `verdicts_for_run`/`route_run`, an article with no decision gets a synthetic `ArticleDecision(article_id=a.id, client_id=a.client_id, error="no_decision")` → REVIEW (fail-closed).
  Depends on:
  - M1.1: models above, plus from `app.config`: `load_settings`, `Settings(jev_base_url, jev_api_key, jev_model, ...)`, `PORTFOLIO_CSV`, `NEWS_CACHE_JSON`, `RESULTS_JSON`, `AUDIT_JSONL`, `JEV_CONCURRENCY` (8), `DEFAULT_STRICTNESS` (0.5).
  - M1.2: `load_portfolio(path) -> list[Client]`, `load_cache(path) -> tuple[dict[str, list[Article]], list[str]]` from `app.gdelt`.
  - M1.3: `judge_article(client, article, http, *, base_url, api_key, model) -> ArticleDecision` from `app.jev_client`.
  - M1.4: `verdict_for_article`, `status_for_client`, `thresholds_from_strictness` from `app.router`.
  - M1.5: `append_decisions(path, run_id, decisions, verdicts, strictness) -> int` from `app.audit`; `build_receipts(decisions, elapsed_s)`, `format_receipts(r)`, `assert_healthy(r)` from `app.receipts`.
Implementation constraints:
  - `scripts/run_screen.py`, in this order:
    1. `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`; `logging.basicConfig(level=logging.INFO)`; `logging.getLogger("httpx").setLevel(logging.WARNING)`. The first raw Jev response is logged at INFO by `jev_client`.
    2. `settings = load_settings()`; `clients = load_portfolio(PORTFOLIO_CSV)`; `articles_by_client, fetch_failures = load_cache(NEWS_CACHE_JSON)`. If there are `fetch_failures`, print `WARNING fetch_failures (shown as NO_COVERAGE): <ids>`.
    3. Inside `async with httpx.AsyncClient() as http:`, set `judge = lambda c, a: judge_article(c, a, http, base_url=settings.jev_base_url, api_key=settings.jev_api_key, model=settings.jev_model)`, then `run = await screen(clients, articles_by_client, judge, concurrency=JEV_CONCURRENCY)`.
    4. `t = thresholds_from_strictness(DEFAULT_STRICTNESS)`; `verdicts = verdicts_for_run(run, t)`; `results = route_run(run, t)`.
    5. `run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")`; `append_decisions(AUDIT_JSONL, run_id, run.decisions, verdicts, DEFAULT_STRICTNESS)`. RESOLVED: the audit is always written, even for an unhealthy run.
    6. Print a table with header `client | status | reason`, then one row per client: `f"{name:<22} | {status.value:<11} | {reasons[0] if reasons else ''}"`. For example, collision clients show `CLEAR | different person` and documented cases show `FLAGGED | fraud, severity 2`.
    7. `print(format_receipts(run.receipts))`.
    8. `assert_healthy(run.receipts)`.
    9. Only after the check passes: `save_run(RESULTS_JSON, run)` and print `Wrote data/results.json`. RESOLVED: a broken run (≥20% Jev errors) never overwrites a good `results.json`.
  - Health check (copied from the plan): `assert receipts.jev_errors < 0.2 * max(receipts.jev_calls, 1), f"{receipts.jev_errors}/{receipts.jev_calls} Jev failures"`. It catches a silent all-REVIEW portfolio caused by a parsing bug.
  - Design rules: Jev judges, code decides. Thresholds live only in the router. Store raw probabilities, never thresholded values. Use one Jev call per (client, article).
Acceptance tests (`tests/test_screening.py`, fake `judge` functions, no HTTP):
  1. 3 clients (C1: 2 articles, C2: 1 article, C3: absent from dict), with a fake judge returning a HIT-shaped decision for C1's articles (`same_person=0.9, is_subject=0.9, risk_type="fraud", risk_conf=0.9, severity=2, severity_conf=0.8, input_tokens=100`) and `same_person=0.1` for C2's → `len(run.clients)==3`, `len(run.decisions)==3`, and `run.receipts.jev_calls==3`. Then `[r.status for r in route_run(run, Thresholds())] == [FLAGGED, CLEAR, NO_COVERAGE]`.
  2. Concurrency: 20 articles and a fake judge that increments an in-flight counter, `await asyncio.sleep(0.01)`, then decrements → the max in-flight is ≤ 8 and > 1.
  3. A judge that raises `ValueError` for one article → that decision has `error=="exception:ValueError"`, and the others are unaffected. `screen` does not raise.
  4. `route_run` on a run whose decision list is missing one article → that client's status is REVIEW, and `verdicts_for_run` gives `(REVIEW, "fail-closed: no_decision")`.
  5. A `save_run(tmp/"r.json", run)` then `load_run` round trip → `== run`.
Done when: tests pass. Then `python scripts/run_screen.py` logs the first raw Jev JSON, prints the table and receipts line, passes the health check, writes `data/results.json`, and grows `data/audit.jsonl` by N lines.