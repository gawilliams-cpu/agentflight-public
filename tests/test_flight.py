import pytest

from agentflight.store import leg_by_id
from agentflight.tools.flight import (block_minutes, check_range_and_flight_time, distance_nm,
                                      leg_times)


@pytest.mark.parametrize("origin, destination, nm", [
    ("TEB", "BED", 158),
    ("BED", "PBI", 1042),
    ("TEB", "PBI", 902),
    ("BED", "APF", 1107),
    ("BED", "SAN", 2231),
])
def test_reference_distances(state, origin, destination, nm):
    assert round(distance_nm(state, origin, destination)) == nm


def test_demo_leg_block_times(state):
    assert block_minutes(state, "N307LH", "TEB", "BED") == 40
    assert block_minutes(state, "N307LH", "BED", "PBI") == 163  # 2 h 43 min


def test_demo_leg_arrivals(state):
    _, arrival_1 = leg_times(state, leg_by_id(state, "A1-1"))
    _, arrival_2 = leg_times(state, leg_by_id(state, "A1-2"))
    assert f"{arrival_1:%H:%M}" == "08:40"
    assert f"{arrival_2:%H:%M}" == "18:43"


def test_naples_is_within_phenom_range(state):
    result = check_range_and_flight_time(state, "N307LH", "BED", "APF")
    assert result["result"] == "pass"
    assert result["details"]["distance_nm"] == 1107


def test_san_diego_is_beyond_phenom_range(state):
    result = check_range_and_flight_time(state, "N307LH", "BED", "SAN")
    assert result["result"] == "fail"
    assert "by 431 nm" in result["summary"]


def test_san_diego_is_within_challenger_range(state):
    assert check_range_and_flight_time(state, "N350LH", "BED", "SAN")["result"] == "pass"


def test_unknown_airport_is_an_error(state):
    with pytest.raises(ValueError, match="Unknown airport"):
        distance_nm(state, "BED", "XYZ")
