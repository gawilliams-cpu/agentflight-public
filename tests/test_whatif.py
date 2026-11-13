import copy

import pytest

from agentflight.store import leg_by_id
from agentflight.tools.checks import check_aircraft_schedule
from agentflight.tools.duty import check_crew_duty
from agentflight.whatif import apply_changes
from tests.conftest import OPTION_B


def test_checks_never_change_the_real_state(state):
    before = copy.deepcopy(state)
    check_crew_duty(state, OPTION_B)
    check_aircraft_schedule(state, "N307LH", [{"op": "move_trip", "trip_id": "A1", "date": "2026-10-30"}])
    assert state == before


def test_move_trip_keeps_times_of_day(state):
    proposed = apply_changes(state, [{"op": "move_trip", "trip_id": "A1", "date": "2026-10-28"}])
    assert leg_by_id(proposed, "A1-1")["departure"] == "2026-10-28T08:00"
    assert leg_by_id(proposed, "A1-2")["departure"] == "2026-10-28T16:00"


def test_set_passengers(state):
    proposed = apply_changes(state, [{"op": "set_passengers", "leg_id": "A1-2",
                                      "passengers": ["A", "B", "C", "D", "E", "F"]}])
    entry = next(e for e in proposed["trips"][0]["legs"] if e["leg"] == "A1-2")
    assert len(entry["passengers"]) == 6


@pytest.mark.parametrize("bad_change", [
    {"op": "teleport"},
    {"op": "move_leg", "leg_id": "NOPE", "departure": "2026-10-29T17:00"},
    {"op": "move_leg", "leg_id": "A1-2", "departure": "5pm Thursday"},
    {"op": "assign_crew", "leg_ids": ["A1-2"], "crew_id": "nobody"},
    {"op": "set_passengers", "leg_id": "P-1028", "passengers": []},
])
def test_bad_changes_are_errors(state, bad_change):
    with pytest.raises(ValueError):
        apply_changes(state, [bad_change])


def test_missing_field_is_a_clear_error(state):
    with pytest.raises(ValueError, match="missing departure"):
        apply_changes(state, [{"op": "move_leg", "leg_id": "A1-2"}])
