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
