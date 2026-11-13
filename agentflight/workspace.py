"""The working copy of the operation, saved between commands and page visits.

data/ is the read-only base week. The four operational files (schedule, trips,
queue, outbox) that approvals and sends change are saved in runtime/. Reference
data (fleet, crew, vendors, rate card, ...) is always read from data/.
Reset deletes the runtime copy, so the next load starts from the base week again.
The eval runner never uses this: every eval run starts from data/.
"""

import json
import shutil

from agentflight import config
from agentflight.store import OPERATIONAL_FILES, load_state


def load_working_state() -> dict:
    state = load_state()
    for name in OPERATIONAL_FILES:
        path = config.RUNTIME_DIR / f"{name}.json"
        if path.exists():
            state[name] = json.loads(path.read_text())
    return state


def save_working_state(state: dict) -> None:
    config.RUNTIME_DIR.mkdir(exist_ok=True)
    for name in OPERATIONAL_FILES:
        path = config.RUNTIME_DIR / f"{name}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state[name], indent=2, default=str))
        temporary.replace(path)  # never leave a half-written file behind


def reset() -> None:
    """Back to the base week: an empty outbox and the pre-seeded queue."""
    if config.RUNTIME_DIR.exists():
        shutil.rmtree(config.RUNTIME_DIR)
