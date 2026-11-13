"""The code's own verification of what the agent proposes."""

from agentflight.agent.package import build_package
from agentflight.whatif import apply_changes
from tests.conftest import CHANGE_1, OPTION_A, OPTION_B, move

CLIENT = "morgan.pryce@haldenridge.example.com"


def run_with(*outputs, stopped=None):
    return {"outputs": [{"tool": t, "input": i} for t, i in outputs], "trace": [], "stopped": stopped}


def message(to, body="Confirmed.", role="client"):
    return {"to": to, "recipient_role": role, "subject": "Thursday", "body": body}


def change_package(**overrides):
    proposal = {
        "trip_id": "A1",
        "summary": "Push leg 2 to 17:00.",
        "record_updates": CHANGE_1,
        "fees": [{"charge": "departure_time_change", "leg_ids": ["A1-2"]}],
        "messages": [message(CLIENT, "New departure 17:00. Change fee: $500.")],
    }
    return {**proposal, **overrides}


def test_demo_change_1_package_verifies_clean(state):
    package = build_package(state, run_with(("propose_change_package", change_package())))
    assert package["problems"] == []
    assert (package["status"], package["reason"]) == ("Needs approval", "Change package")
    assert package["change_package"]["fee_total"] == 500
    assert all(c["result"] == "pass" for c in package["change_package"]["verified_checks"])


def test_invented_address_is_a_problem(state):
    package = build_package(state, run_with(("propose_change_package",
                                             change_package(messages=[message("ops@palmbeach-fbo.com", role="fbo")]))))
    assert any("not in the data" in p for p in package["problems"])


def test_wrong_fee_in_a_message_is_a_problem(state):
    package = build_package(state, run_with(("propose_change_package",
                                             change_package(messages=[message(CLIENT, "Change fee: $750.")]))))
    assert any("$750" in p for p in package["problems"])


def test_another_clients_contact_is_a_problem(state):
    package = build_package(state, run_with(("propose_change_package", change_package(
        messages=[message("riley.novak@ostravabio.example.com")]))))
    assert any("not a contact of this trip's client" in p for p in package["problems"])


def test_undefined_charge_is_a_problem(state):
    package = build_package(state, run_with(("propose_change_package",
                                             change_package(fees=[{"charge": "pet_fee"}]))))
    assert any("No 'pet_fee' charge" in p for p in package["problems"])


def test_update_that_breaks_duty_is_a_problem(state):
    package = build_package(state, run_with(("propose_change_package",
                                             change_package(record_updates=[move("A1-2", "2026-10-29T18:00")]))))
    assert any("fails a check" in p and "over by 13 min" in p for p in package["problems"])


def demo_2_clarification(options):
    return {
        "trip_id": "A1",
        "summary": "18:00 breaks crew duty; two options.",
        "messages": [message(CLIENT, "Option A: $1,000. Option B: $3,000.")],
        "options": options,
    }


def test_demo_change_2_options_both_verify(state):
    after_change_1 = apply_changes(state, CHANGE_1)
    options = [
        {"label": "A", "description": "Move leg 1 to 09:00 too", "changes": OPTION_A,
         "fees": [{"charge": "departure_time_change", "leg_ids": ["A1-1", "A1-2"]}]},
        {"label": "B", "description": "Swap to the BED standby crew", "changes": OPTION_B,
         "fees": [{"charge": "departure_time_change", "leg_ids": ["A1-2"]},
                  {"charge": "crew_swap", "count": 1}]},
    ]
    package = build_package(after_change_1, run_with(("propose_clarification", demo_2_clarification(options))))
    assert package["problems"] == []
    assert (package["status"], package["reason"]) == ("Needs approval", "Options for client")
    totals = {o["label"]: o["fee_total"] for o in package["clarification"]["options"]}
    assert totals == {"A": 1000, "B": 3000}


def test_half_done_option_b_is_caught(state):
    after_change_1 = apply_changes(state, CHANGE_1)
    options = [{"label": "B", "description": "Swap crew for leg 2 only",
                "changes": [move("A1-2", "2026-10-29T18:00"),
                            {"op": "assign_crew", "leg_ids": ["A1-2"], "crew_id": "nandakumar-kessler"}],
                "fees": [{"charge": "crew_swap", "count": 1}]}]
    clarification = demo_2_clarification(options)
    clarification["messages"] = [message(CLIENT, "Option B: $2,500.")]
    package = build_package(after_change_1, run_with(("propose_clarification", clarification)))
    assert package["clarification"]["options"][0]["passes"] is False
    assert any("departs PBI" in p for p in package["problems"])


def test_sales_routing_waits_on_sales(state):
    routing = {"trip_id": "A1", "summary": "8 passengers exceed the Phenom.",
               "messages": [{"subject": "Re-quote: 8 passengers", "body": "Needs a Challenger quote."}],
               "held_updates": [{"party": "BED caterer", "update": "Catering for 8"}]}
    package = build_package(state, run_with(("route_to_sales", routing)))
    assert (package["status"], package["reason"]) == ("Waiting", "Routed to sales")
    assert package["sales_routing"]["messages"][0]["to"] == "sales@larkhaven.example.com"


def test_stopped_run_needs_review(state):
    package = build_package(state, run_with(stopped="Tool get_trip failed"))
    assert (package["status"], package["reason"]) == ("Needs review", "Run stopped")
    assert package["problems"] == ["Tool get_trip failed"]


def test_a_problem_turns_approval_into_review(state):
    package = build_package(state, run_with(("propose_change_package",
                                             change_package(fees=[{"charge": "pet_fee"}]))))
    assert (package["status"], package["reason"]) == ("Needs review", "Change package")


def test_plain_question_for_client(state):
    question = {"summary": "Which trip?", "messages": [message(CLIENT, "Thursday or Sunday?")]}
    package = build_package(state, run_with(("propose_clarification", question)))
    assert (package["status"], package["reason"]) == ("Needs approval", "Question for client")


def test_a_later_proposal_replaces_an_earlier_one(state):
    first = change_package(summary="First try.", messages=[message(CLIENT, "Typo $50.")])
    second = change_package(summary="Corrected.")
    package = build_package(state, run_with(("propose_change_package", first), ("propose_change_package", second)))
    assert package["change_package"]["summary"] == "Corrected."
    assert package["problems"] == []   # the replaced draft's $50 doesn't count
