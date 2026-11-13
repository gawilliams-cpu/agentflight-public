"""Settings in one place. Change the model or a prompt version here, then re-run the evals."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Read KEY=value lines from .env (git-ignored) into the environment, without
    overriding anything already set. Keeps the API key out of the code."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ROOT / ".env")

MODEL = "claude-sonnet-5-5"
EFFORT = "medium"           # low | medium | high | xhigh | max; tune with the evals
MAX_TOKENS = 16000          # per model response
MAX_TURNS = 30              # a run that needs more is stopped and flagged

AGENT_PROMPT = ROOT / "prompts" / "agent-system-v8.md"
AGENT_TOOLS = ROOT / "prompts" / "tools-v7.json"

CHAT_PROMPT = ROOT / "prompts" / "chat-system-v2.md"
CHAT_TOOLS = ROOT / "prompts" / "chat-tools-v1.json"
CHAT_EFFORT = "medium"      # low gave sloppy self-corrections ("13 h 13 min... correction"); medium is clean
CHAT_MAX_TURNS = 10

INBOX_DIR = ROOT / "data" / "inbox"
RUNS_DIR = ROOT / "runs"
RUNTIME_DIR = ROOT / "runtime"     # the working copy of schedule, trips, queue, outbox

# US dollars per million tokens, for the cost line on every run.
PRICES = {
    "claude-sonnet-5-5": {"input": 2.00, "cache_write": 2.50, "cache_read": 0.20, "output": 10.00},
}

# If a safety classifier declines a request, the API re-runs it on a suitable
# fallback model in the same call instead of stopping.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
