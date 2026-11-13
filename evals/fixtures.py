"""Saved moments of the demo week that eval cases can start from.

    uv run python -m evals.fixtures after-demo-2    build it with the real agent (about 16¢)

"after-demo-2": demo change 1 arrived and was approved; demo change 2 arrived and
its options are waiting for the trip manager. The trip chat cases (H1–H4) start here.
"options-sent": after-demo-2, with the options approved and emailed to the client
(no API cost; built from after-demo-2). The client-reply cases (4a, 4b) start here.
Only the operational files are saved; reference data always comes from data/.
"""

import json
import sys
from pathlib import Path

from agentflight import config
from agentflight.store import OPERATIONAL_FILES, load_state
from agentflight.workflow import approve, receive

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def build_after_demo_2() -> dict:
    state = load_state()
    for message_id, approve_it in (("demo-1", True), ("demo-2", False)):
        message = json.loads((config.INBOX_DIR / f"{message_id}.json").read_text())
        change, _ = receive(state, "email", message["from"], message["subject"], message["body"])
        if approve_it:
            approve(state, change["id"])
    return state


def build_options_sent() -> dict:
    state = load_fixture(load_state(), "after-demo-2")
    options = next(c for c in state["queue"] if c["status"] == "Needs approval" and c["trip"] == "A1")
    approve(state, options["id"])
    return state


BUILDERS = {"after-demo-2": build_after_demo_2, "options-sent": build_options_sent}


def save_fixture(name: str) -> Path:
    state = BUILDERS[name]()
    folder = FIXTURES_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    for file in OPERATIONAL_FILES:
        (folder / f"{file}.json").write_text(json.dumps(state[file], indent=1, default=str) + "\n")
    return folder


def load_fixture(state: dict, name: str) -> dict:
    """The base week with a saved moment's operational files laid over it."""
    folder = FIXTURES_DIR / name
    if not folder.exists():
        raise SystemExit(f"No fixture {name!r}. Build it: uv run python -m evals.fixtures {name}")
    for file in OPERATIONAL_FILES:
        state[file] = json.loads((folder / f"{file}.json").read_text())
    return state


if __name__ == "__main__":
    for name in sys.argv[1:] or BUILDERS:
        print(f"Saved {save_fixture(name)}")
