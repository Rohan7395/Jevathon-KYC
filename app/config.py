import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
PORTFOLIO_CSV = DATA_DIR / "portfolio.csv"
NEWS_CACHE_JSON = DATA_DIR / "news_cache.json"
RESULTS_JSON = DATA_DIR / "results.json"
AUDIT_JSONL = DATA_DIR / "audit.jsonl"

DEFAULT_STRICTNESS = 0.5
JEV_DEFAULT_MODEL = "jev-latest"
JEV_TIMEOUT_S = 10.0
JEV_RETRY_BACKOFF_S = 1.0
JEV_CONCURRENCY = 8
ARTICLE_TEXT_MAX_CHARS = 1500

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MAXRECORDS = 25
GDELT_THROTTLE_S = 5.0
GDELT_NONJSON_RETRY_WAIT_S = 10.0
GDELT_HTTP_TIMEOUT_S = 20.0

JEV_PRICE_PER_INPUT_TOKEN = 4.2e-8          # $42 per 1B input tokens; output is free
LLM_EQUIV_INPUT_PRICE_PER_TOKEN = 3e-6       # $3 / 1M input tokens
LLM_EQUIV_OUTPUT_PRICE_PER_TOKEN = 1.5e-5    # $15 / 1M output tokens
LLM_EQUIV_OUTPUT_TOKENS_PER_CALL = 150
HEALTH_MAX_ERROR_RATIO = 0.2


@dataclass(frozen=True)
class Settings:
    jev_base_url: str
    jev_api_key: str
    jev_model: str
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None


def load_settings(require_jev: bool = True) -> Settings:
    load_dotenv()

    jev_base_url = os.environ.get("JEV_BASE_URL", "") or ""
    jev_api_key = os.environ.get("JEV_API_KEY", "") or ""
    jev_model = os.environ.get("JEV_MODEL") or JEV_DEFAULT_MODEL

    if require_jev:
        if not jev_base_url:
            raise RuntimeError("Missing env var: JEV_BASE_URL")
        if not jev_api_key:
            raise RuntimeError("Missing env var: JEV_API_KEY")

    jev_base_url = jev_base_url.rstrip("/")

    llm_base_url = os.environ.get("LLM_BASE_URL") or None
    llm_api_key = os.environ.get("LLM_API_KEY") or None
    llm_model = os.environ.get("LLM_MODEL") or None

    return Settings(
        jev_base_url=jev_base_url,
        jev_api_key=jev_api_key,
        jev_model=jev_model,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
    )
