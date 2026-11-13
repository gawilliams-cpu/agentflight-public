import pytest

from agentflight.store import load_state


@pytest.fixture
def state():
    """A fresh copy of the base demo-week state for each test."""
    return load_state()


def move(leg_id, departure):
    return {"op": "move_leg", "leg_id": leg_id, "departure": departure}


# The changes behind the demo, written once so every test uses the same ones.
CHANGE_1 = [move("A1-2", "2026-10-29T17:00")]
PUSH_TO_1730 = [move("A1-2", "2026-10-29T17:30")]
PUSH_TO_1800 = [move("A1-2", "2026-10-29T18:00")]
OPTION_A = [move("A1-1", "2026-10-29T09:00"), move("A1-2", "2026-10-29T18:00")]
# Option B: the BED standby crew flies leg 2, overnights at PBI, and so also takes
# the Phenom's Friday flights. Whitlock/Reyes end Thursday at BED.
OPTION_B = [
    move("A1-2", "2026-10-29T18:00"),
    {"op": "assign_crew", "leg_ids": ["A1-2", "P-1030", "C-1", "C-2"], "crew_id": "nandakumar-kessler"},
]


def duty_day(result, crew_id, date):
    crew = next(c for c in result["details"]["crews"] if c["crew"] == crew_id)
    return next(d for d in crew["duty_days"] if d["date"] == date)


def rest_before(result, crew_id, date):
    crew = next(c for c in result["details"]["crews"] if c["crew"] == crew_id)
    return next(r for r in crew["rest_periods"] if r["before"] == date)
