from agentflight.tools.checks import (check_aircraft_availability, check_aircraft_schedule,
                                      check_airport_hours, check_capacity, check_watchlist,
                                      find_available_crew)
from tests.conftest import move


# Capacity: case 5 / C2 boundary on the 7-seat Phenom.
def test_seven_passengers_fit(state):
    assert check_capacity(state, "N307LH", 7)["result"] == "pass"


def test_eight_passengers_do_not_fit(state):
    result = check_capacity(state, "N307LH", 8)
    assert result["result"] == "fail"
    assert "by 1" in result["summary"]


# Airport hours
def test_airport_open_24_hours(state):
    assert check_airport_hours(state, "BED", "2026-10-29T03:00")["result"] == "pass"


def test_airport_closed_at_night(state):
    assert check_airport_hours(state, "ACK", "2026-10-29T23:30")["result"] == "fail"
    assert check_airport_hours(state, "ACK", "2026-10-29T22:59")["result"] == "pass"


# Aircraft availability: C2's capacity analysis.
def test_challenger_is_free_at_bed_thursday_afternoon(state):
    result = check_aircraft_availability(state, "N350LH", "2026-10-29T16:00", "2026-10-29T21:00")
    assert result["result"] == "pass"
    assert result["details"]["location_at_start"] == "BED"


def test_citation_is_free_thursday_afternoon_but_at_teb(state):
    result = check_aircraft_availability(state, "N560LH", "2026-10-29T16:00", "2026-10-29T21:00")
    assert result["result"] == "pass"
    assert result["details"]["location_at_start"] == "TEB"


def test_phenom_is_busy_during_maintenance(state):
    result = check_aircraft_availability(state, "N307LH", "2026-10-27T10:00", "2026-10-27T12:00")
    assert result["result"] == "fail"
    assert "maintenance" in result["summary"]


# Aircraft schedule: B5 and C5.
def test_moving_the_trip_to_wednesday_works(state):
    changes = [
        {"op": "move_trip", "trip_id": "A1", "date": "2026-10-28"},
        move("P-1028", "2026-10-27T18:00"),  # positioning moves to Tuesday evening, after maintenance
    ]
    assert check_aircraft_schedule(state, "N307LH", changes)["result"] == "pass"


def test_moving_the_trip_to_friday_conflicts_with_client_c(state):
    result = check_aircraft_schedule(state, "N307LH",
                                     [{"op": "move_trip", "trip_id": "A1", "date": "2026-10-30"}])
    assert result["result"] == "fail"
    assert "Client C" in result["summary"]


def test_option_a_keeps_the_phenom_schedule_intact(state):
    changes = [move("A1-1", "2026-10-29T09:00"), move("A1-2", "2026-10-29T18:00")]
    assert check_aircraft_schedule(state, "N307LH", changes)["result"] == "pass"


# Available crew: option B and C2.
def test_standby_crew_is_available_at_bed_for_leg_2(state):
    result = find_available_crew(state, "BED", "phenom_300", "2026-10-29T17:00", "2026-10-29T21:13")
    names = [c["crew"] for c in result["details"]["candidates"]]
    assert names == ["nandakumar-kessler"]  # Whitlock/Reyes are busy flying leg 2


def test_challenger_crew_is_available_at_bed_thursday(state):
    result = find_available_crew(state, "BED", "challenger_350", "2026-10-29T15:00", "2026-10-29T21:00")
    assert [c["crew"] for c in result["details"]["candidates"]] == ["brooks-okafor"]


def test_no_citation_crew_at_bed(state):
    result = find_available_crew(state, "BED", "citation_xls", "2026-10-29T15:00", "2026-10-29T21:00")
    assert result["result"] == "fail"


# Watchlist
def test_watchlist_match_ignores_case_and_spacing(state):
    result = check_watchlist(state, ["Alex Halden", "  viktor   SELDEN "])
    assert result["result"] == "fail"
    assert result["details"]["matches"] == ["  viktor   SELDEN "]


def test_watchlist_clean(state):
    assert check_watchlist(state, ["Alex Halden", "Jordan Ellery"])["result"] == "pass"
