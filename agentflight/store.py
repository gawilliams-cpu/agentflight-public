"""Loads the JSON files in data/ and looks records up by id.

The state is a plain dict: one key per data file. Lookups raise ValueError for
an unknown id, so a bad id stops a run instead of being guessed around.
"""

import copy
import json
from datetime import datetime
from pathlib import Path

from agentflight.times import parse

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DATA_FILES = (
    "clock",
    "operator",
    "fleet",
    "crew",
    "airports",
    "vendors",
    "clients",
    "schedule",
    "trips",
    "queue",
    "rate_card",
    "policies",
    "watchlist",
    "outbox",
)

# The files that change as the operation runs (approvals, sends, the queue).
# Everything else in data/ is reference data and never changes.
OPERATIONAL_FILES = ("schedule", "trips", "queue", "outbox")


def load_state(data_dir: Path = DATA_DIR) -> dict:
    """The base demo week from data/. Read-only: nothing writes back to data/."""
    state = {}
    for name in DATA_FILES:
        with open(data_dir / f"{name}.json") as f:
            state[name] = json.load(f)
    return state


def copy_state(state: dict) -> dict:
    """A fully independent copy, for what-if checks."""
    return copy.deepcopy(state)


def now(state: dict) -> datetime:
    return parse(state["clock"]["now"])


def _find(items: list, key: str, value: str, what: str) -> dict:
    for item in items:
        if item[key] == value:
            return item
    raise ValueError(f"Unknown {what}: {value!r}")


def aircraft_by_tail(state, tail):
    return _find(state["fleet"], "tail", tail, "aircraft")


def crew_by_id(state, crew_id):
    return _find(state["crew"], "id", crew_id, "crew")


def airport_by_code(state, code):
    return _find(state["airports"], "code", code, "airport")


def service_by_id(state, service_id):
    for trip in state["trips"]:
        for service in trip["services"]:
            if service["id"] == service_id:
                return service
    raise ValueError(f"Unknown service: {service_id!r}")


def leg_by_id(state, leg_id):
    return _find(state["schedule"]["legs"], "id", leg_id, "leg")


def trip_by_id(state, trip_id):
    return _find(state["trips"], "id", trip_id, "trip")


def client_by_id(state, client_id):
    return _find(state["clients"], "id", client_id, "client")


def vendor_by_id(state, vendor_id):
    return _find(state["vendors"], "id", vendor_id, "vendor")
