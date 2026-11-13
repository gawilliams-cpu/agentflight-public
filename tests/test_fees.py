import pytest

from agentflight.tools.fees import compute_change_fee

# The clock is Tue 10/27 09:00. Leg A1-2 departs Thu 10/29 16:00: 55 h notice.


def fee(state, charge, **kwargs):
    return compute_change_fee(state, charge, **kwargs)["amount"]


def test_demo_change_1_is_500(state):
    result = compute_change_fee(state, "departure_time_change", leg_ids=["A1-2"])
    assert result["amount"] == 500
    assert "55 h notice → 24–72 h tier → $500" in result["summary"]


def test_option_a_is_1000(state):
    # Both legs move: A1-1 (47 h notice) and A1-2 (55 h notice), $500 each.
    assert fee(state, "departure_time_change", leg_ids=["A1-1", "A1-2"]) == 1000


def test_option_b_is_3000(state):
    total = (fee(state, "departure_time_change", leg_ids=["A1-2"])
             + fee(state, "crew_swap", count=1))
    assert total == 3000


def set_clock(state, now):
    state["clock"]["now"] = now


@pytest.mark.parametrize("now, notice, amount", [
    ("2026-10-26T16:00", "exactly 72 h", 0),
    ("2026-10-26T16:01", "71 h 59 min", 500),
    ("2026-10-28T16:00", "exactly 24 h", 500),
    ("2026-10-28T16:01", "23 h 59 min", 1500),
    ("2026-10-29T15:59", "1 min", 1500),
])
def test_tier_boundaries(state, now, notice, amount):
    set_clock(state, now)
    assert fee(state, "departure_time_change", leg_ids=["A1-2"]) == amount, notice


def test_date_change_is_per_trip(state):
    # Trip A1's first departure is Thu 08:00: 47 h notice, 24–72 h tier.
    assert fee(state, "date_change", trip_id="A1") == 2500


def test_flat_charges(state):
    assert fee(state, "catering_change") == 0
    assert fee(state, "crew_overnight", count=2) == 1200


def test_undefined_charge_is_not_zero(state):
    result = compute_change_fee(state, "pet_fee")
    assert result["defined"] is False
    assert result["amount"] is None
    assert "do not invent" in result["summary"]


def test_per_leg_charge_needs_legs(state):
    with pytest.raises(ValueError):
        compute_change_fee(state, "departure_time_change")


def test_past_departure_is_an_error(state):
    set_clock(state, "2026-10-29T17:00")
    with pytest.raises(ValueError):
        compute_change_fee(state, "departure_time_change", leg_ids=["A1-2"])
