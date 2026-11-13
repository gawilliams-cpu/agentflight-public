from agentflight.views import schedule_board
from agentflight.whatif import apply_changes


def test_board_covers_the_demo_week_by_aircraft_and_pilot(state):
    board = schedule_board(state)
    assert [d["label"] for d in board["days"]][0] == "Mon 10/26" and len(board["days"]) == 7
    phenom = next(r for r in board["aircraft"] if r["id"] == "N307LH")
    kinds = {i["kind"] for i in phenom["items"]}
    assert kinds == {"client", "positioning", "maintenance"}
    a1_2 = next(i for i in phenom["items"] if i.get("leg") == "A1-2")
    assert (a1_2["label"], a1_2["end"]) == ("BED→PBI · Halden", "2026-10-29T18:43")   # arrival from the flight-time tool
    mx = next(i for i in phenom["items"] if i["kind"] == "maintenance")
    assert mx["label"] == "BED"
    assert len(board["pilots"]) == 2 * len(state["crew"])


def test_pilot_rows_carry_standby_and_the_duty_days_the_check_uses(state):
    board = schedule_board(state)
    priya = next(r for r in board["pilots"] if r["id"] == "priya-nandakumar")
    assert [i["kind"] for i in priya["items"]] == ["standby"]
    dana = next(r for r in board["pilots"] if r["id"] == "dana-whitlock")
    thursday = next(d for d in dana["duty"] if d["start"].startswith("2026-10-29"))
    # Report 07:00 (an hour before 08:00), release 19:13 (30 min after the 18:43 arrival).
    assert (thursday["start"], thursday["end"], thursday["label"]) == ("2026-10-29T07:00", "2026-10-29T19:13",
                                                                       "Duty 12 h 13 min")


def test_a_pending_leg_move_is_drawn_as_proposed(state):
    state["queue"].append({"id": "chg-x", "status": "Needs approval", "trip": "A1", "package": {"change_package": {
        "legs": [{"leg": "A1-2", "origin": "BED", "destination": "PBI",
                  "departure": "2026-10-29T17:00", "arrival": "2026-10-29T19:43"}]}}})
    phenom = next(r for r in schedule_board(state)["aircraft"] if r["id"] == "N307LH")
    proposed = [i for i in phenom["items"] if i["kind"] == "proposed"]
    assert [(p["leg"], p["start"], p["label"]) for p in proposed] == [
        ("A1-2", "2026-10-29T17:00", "Proposed: BED→PBI at 17:00")]


def test_board_follows_an_approved_change(state):
    moved = apply_changes(state, [{"op": "move_leg", "leg_id": "A1-2", "departure": "2026-10-29T17:00"}])
    phenom = next(r for r in schedule_board(moved)["aircraft"] if r["id"] == "N307LH")
    assert next(i for i in phenom["items"] if i.get("leg") == "A1-2")["start"] == "2026-10-29T17:00"
