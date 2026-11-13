from agentflight.tools.fixes import find_duty_fixes
from agentflight.whatif import apply_changes
from tests.conftest import CHANGE_1, PUSH_TO_1800, move


def test_demo_change_2_finds_three_fixes(state):
    after_change_1 = apply_changes(state, CHANGE_1)
    fixes = find_duty_fixes(after_change_1, PUSH_TO_1800)["details"]["fixes"]
    found = {f["kind"]: f for f in fixes}

    assert found["latest_legal_time"]["changes"] == [move("A1-2", "2026-10-29T17:45")]
    assert found["latest_legal_time"]["fee_total"] == 500

    assert move("A1-1", "2026-10-29T08:15") in found["move_earlier_leg"]["changes"]
    assert found["move_earlier_leg"]["fee_total"] == 1000

    swap = next(c for c in found["crew_swap"]["changes"] if c["op"] == "assign_crew")
    assert swap == {"op": "assign_crew", "leg_ids": ["A1-2", "P-1030"], "crew_id": "nandakumar-kessler"}
    assert found["crew_swap"]["fee_total"] == 3000


def test_no_fix_needed_when_the_change_passes(state):
    result = find_duty_fixes(state, CHANGE_1)
    assert result["details"]["fixes"] == []
    assert "no fix needed" in result["summary"]


def test_push_to_1900_has_no_fix(state):
    # Every fix breaks Friday's 10 h rest minimum, as worked out on 10/14.
    after_change_1 = apply_changes(state, CHANGE_1)
    result = find_duty_fixes(after_change_1, [move("A1-2", "2026-10-29T19:00")])
    kinds = {f["kind"] for f in result["details"]["fixes"]}
    assert "move_earlier_leg" not in kinds and "crew_swap" not in kinds
