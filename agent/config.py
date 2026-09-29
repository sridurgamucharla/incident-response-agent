"""Central config: every setting is read from the project-root .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    shoplite_database_url: str
    dejavu_database_url: str

    hindsight_api_key: str = ""
    hindsight_base_url: str = ""
    hindsight_bank_id: str = "dejavu-sre"

    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_models: str = "gemini-3.1-flash-lite"  # comma-separated, tried in order
    gemini_rpm: int = 10  # our own pacing, below the free-tier limit
    llm_max_triage_calls: int = 3  # hard cap per triage, tool rounds and repairs included

    shoplite_url: str = "http://localhost:8001"
    log_dir: str = "logs"

    agent_url: str = "http://localhost:8000"
    web_origin: str = "http://localhost:5173"  # the React dashboard (CORS)

    # Sentinel detection thresholds
    sentinel_window_s: int = 30  # sliding window of log lines
    sentinel_tick_s: int = 5  # how often the window is evaluated
    sentinel_min_requests: int = 5  # ignore services with less traffic than this in the window
    sentinel_error_rate: float = 0.2  # share of 5xx responses that counts as a spike
    sentinel_p95_ms: float = 1500  # p95 latency that counts as a spike
    sentinel_cooldown_s: int = 20  # quiet time before an outage is declared resolved

    @property
    def gemini_models(self) -> list[str]:
        """Main model first, then each fallback in order (deduplicated)."""
        names = [self.gemini_model, *self.gemini_fallback_models.split(",")]
        return list(dict.fromkeys(n.strip() for n in names if n.strip()))

    @property
    def log_path(self) -> Path:
        path = Path(self.log_dir)
        return path if path.is_absolute() else ROOT_DIR / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
