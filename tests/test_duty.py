"""The tuning targets in build brief section 6, as exact minutes."""

import pytest

from agentflight.tools.duty import check_crew_duty
from tests.conftest import (CHANGE_1, OPTION_A, OPTION_B, PUSH_TO_1730, PUSH_TO_1800, duty_day,
                            rest_before)

THU, FRI = "2026-10-29", "2026-10-30"


def minutes(h, m):
    return h * 60 + m


def test_base_thursday_is_12h13(state):
    result = check_crew_duty(state, crew_ids=["whitlock-reyes"])
    day = duty_day(result, "whitlock-reyes", THU)
    assert (day["start"], day["end"], day["minutes"]) == ("07:00", "19:13", minutes(12, 13))


@pytest.mark.parametrize("changes, expected, result", [
    (CHANGE_1, minutes(13, 13), "pass"),
    (PUSH_TO_1730, minutes(13, 43), "pass"),
    (PUSH_TO_1800, minutes(14, 13), "fail"),
])
def test_pushing_leg_2(state, changes, expected, result):
    check = check_crew_duty(state, changes)
    assert duty_day(check, "whitlock-reyes", THU)["minutes"] == expected
    assert check["result"] == result


def test_push_to_1800_says_over_by_13_min(state):
    summary = check_crew_duty(state, PUSH_TO_1800)["summary"]
    assert "14 h 13 min against the 14 h limit — over by 13 min" in summary


def test_change_1_leaves_11h47_rest_before_friday(state):
    check = check_crew_duty(state, CHANGE_1)
    assert rest_before(check, "whitlock-reyes", FRI)["minutes"] == minutes(11, 47)


def test_option_a(state):
    check = check_crew_duty(state, OPTION_A)
    assert check["result"] == "pass"
    day = duty_day(check, "whitlock-reyes", THU)
    assert (day["start"], day["minutes"]) == ("08:00", minutes(13, 13))
    assert rest_before(check, "whitlock-reyes", FRI)["minutes"] == minutes(10, 47)


def test_option_b(state):
    check = check_crew_duty(state, OPTION_B)
    assert check["result"] == "pass"
    thursday = duty_day(check, "nandakumar-kessler", THU)
    assert (thursday["start"], thursday["end"], thursday["minutes"]) == ("12:00", "21:13", minutes(9, 13))
    assert rest_before(check, "nandakumar-kessler", FRI)["minutes"] == minutes(10, 47)
    assert duty_day(check, "nandakumar-kessler", FRI)["minutes"] == minutes(11, 7)
    # Whitlock/Reyes fly only leg 1 and are checked too, since they lose leg 2.
    assert duty_day(check, "whitlock-reyes", THU)["minutes"] == minutes(2, 10)


def test_standby_alone_counts_as_duty(state):
    check = check_crew_duty(state, crew_ids=["nandakumar-kessler"])
    assert duty_day(check, "nandakumar-kessler", THU)["minutes"] == minutes(8, 0)


def test_short_rest_fails(state):
    # Leg 2 at 22:00 lands 00:43 Friday; the Friday 08:00 report leaves under 10 h rest.
    check = check_crew_duty(state, [{"op": "move_leg", "leg_id": "A1-2",
                                     "departure": "2026-10-29T21:00"}])
    assert check["result"] == "fail"
    assert "under the 10 h minimum" in check["summary"]


def test_wrong_type_rating_fails(state):
    check = check_crew_duty(state, [{"op": "assign_crew", "leg_ids": ["A1-2"],
                                     "crew_id": "brooks-okafor"}])
    assert check["result"] == "fail"
    assert "not rated on the Phenom 300" in check["summary"]


def test_swapping_only_leg_2_strands_the_friday_flights(state):
    # Option B done halfway: Whitlock/Reyes end Thursday at BED, but the
    # Phenom's Friday positioning leg still lists them and departs PBI.
    check = check_crew_duty(state, [
        {"op": "move_leg", "leg_id": "A1-2", "departure": "2026-10-29T18:00"},
        {"op": "assign_crew", "leg_ids": ["A1-2"], "crew_id": "nandakumar-kessler"},
    ])
    assert check["result"] == "fail"
    assert "Whitlock/Reyes would be at BED, but leg P-1030 departs PBI" in check["summary"]


def test_needs_crew_or_changes(state):
    with pytest.raises(ValueError):
        check_crew_duty(state)
