"""The base demo week holds together before any change arrives."""

import json
import re

import pytest

from agentflight.store import trip_by_id
from agentflight.tools.checks import check_aircraft_schedule, check_airport_hours, check_capacity
from agentflight.tools.duty import check_crew_duty
from agentflight.tools.flight import leg_times
from agentflight.times import to_text


def test_every_crew_day_is_legal(state):
    crew_ids = [crew["id"] for crew in state["crew"]]
    result = check_crew_duty(state, crew_ids=crew_ids)
    assert result["result"] == "pass", result["summary"]


@pytest.mark.parametrize("tail", ["N307LH", "N350LH", "N560LH"])
def test_every_aircraft_schedule_holds_together(state, tail):
    result = check_aircraft_schedule(state, tail)
    assert result["result"] == "pass", result["summary"]


def test_no_leg_falls_outside_airport_hours(state):
    for leg in state["schedule"]["legs"]:
        departure, arrival = leg_times(state, leg)
        assert check_airport_hours(state, leg["origin"], to_text(departure))["result"] == "pass"
        assert check_airport_hours(state, leg["destination"], to_text(arrival))["result"] == "pass"


def test_every_leg_fits_its_aircraft(state):
    for leg in state["schedule"]["legs"]:
        if leg["trip"]:
            entry = next(e for e in trip_by_id(state, leg["trip"])["legs"] if e["leg"] == leg["id"])
            assert check_capacity(state, leg["aircraft"], len(entry["passengers"]))["result"] == "pass"


def test_every_trip_leg_is_in_the_schedule(state):
    scheduled = {leg["id"] for leg in state["schedule"]["legs"]}
    for trip in state["trips"]:
        for entry in trip["legs"]:
            assert entry["leg"] in scheduled


def test_no_passenger_is_on_the_watchlist(state):
    listed = {name.lower() for name in state["watchlist"]["names"]}
    for trip in state["trips"]:
        for entry in trip["legs"]:
            assert not listed & {p.lower() for p in entry["passengers"]}


def test_all_email_addresses_are_example_domains(state):
    text = json.dumps(state)
    for address in re.findall(r"[\w.+-]+@([\w.-]+)", text):
        assert address.endswith("example.com"), address
