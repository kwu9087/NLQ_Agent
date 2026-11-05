"""Settings in one place. Each can be overridden with an environment variable."""

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from .env (git-ignored) into the environment.

    Variables already set in the shell win, so .env only fills gaps.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


_load_dotenv(ROOT / ".env")

DB_PATH = Path(os.environ.get("NLQ_DB_PATH", ROOT / "data" / "tickit.db"))
VIEWS_SQL_PATH = ROOT / "scripts" / "views.sql"
SQL_PROMPT_PATH = ROOT / "prompts" / "system_prompt.md"
ANSWER_PROMPT_PATH = ROOT / "prompts" / "answer_prompt.md"

# Model (see README Part 3). Effort is the only depth control on Claude Opus 5.5.
MODEL = os.environ.get("NLQ_MODEL", "claude-opus-5-5")
SQL_EFFORT = os.environ.get("NLQ_SQL_EFFORT", "medium")
ANSWER_EFFORT = os.environ.get("NLQ_ANSWER_EFFORT", "low")
SQL_MAX_TOKENS = 16000
ANSWER_MAX_TOKENS = 8000
# Server-side refusal fallback: if the model declines, the API reruns the request
# on Anthropic's recommended fallback model instead of returning a refusal.
USE_FALLBACKS = os.environ.get("NLQ_USE_FALLBACKS", "1") == "1"

# Query execution limits.
MAX_ROWS = 100            # rows returned to the user
ANSWER_ROWS = 50          # rows shown to the model when it writes the answer
QUERY_TIMEOUT_S = 5.0
MAX_SQL_ATTEMPTS = 2      # first try plus one repair after an SQL error


def today() -> date:
    """Today's date, or NLQ_TODAY (YYYY-MM-DD) to pin it for tests and demos."""
    pinned = os.environ.get("NLQ_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()
