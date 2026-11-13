"""The trip chat: read-only by construction, tested with a scripted model (no API cost)."""

import copy

import pytest

from agentflight.chat import ask, chat_tools
from agentflight.workflow import receive
from tests.fake_claude import FakeClaude, response, text, tool_use
from tests.test_workflow import demo_1_package, scripted

CLIENT = "morgan.pryce@haldenridge.example.com"


def with_open_change(state):
    change, _ = receive(state, "email", CLIENT, "Thursday", "Push to 5pm",
                        client=scripted("propose_change_package", demo_1_package()))
    return change


def test_chat_has_no_tool_that_changes_anything():
    _, tools = chat_tools()
    names = {t["name"] for t in tools}
    assert "check_crew_duty" in names and "suggest_revision" in names
    assert not names & {"propose_change_package", "propose_clarification", "route_to_sales", "flag_message"}


def test_a_what_if_runs_the_real_check_and_changes_nothing(state):
    with_open_change(state)
    before = copy.deepcopy(state)
    claude = FakeClaude(
        response(tool_use("check_crew_duty", {"changes": [{"op": "move_leg", "leg_id": "A1-2",
                                                           "departure": "2026-10-29T17:30"}]})),
        response(text("Yes: crew duty would be 13 h 43 min, under the 14 h limit.")))
    result = ask(state, "A1", "Would 17:30 work?", client=claude)
    assert result["tools_used"] == ["check_crew_duty"]
    assert result["trace"][0]["result"]["result"] == "pass"
    assert "13 h 43 min" in result["answer"]
    assert state == before


def test_the_chat_sees_the_trip_and_its_open_change(state):
    with_open_change(state)
    claude = FakeClaude(response(text("It's waiting for your approval.")))
    ask(state, "A1", "What's going on with this trip?", client=claude)
    first_message = claude.requests[0]["messages"][0]["content"]
    assert first_message.startswith("<trip_context>")
    assert '"waiting_for_decision"' in first_message and "Push leg 2 to 17:00." in first_message
    assert first_message.rstrip().endswith("What's going on with this trip?")


def test_suggest_revision_only_for_an_open_change(state):
    change = with_open_change(state)
    good = FakeClaude(response(tool_use("suggest_revision", {"change_id": change["id"],
                                                              "instructions": "Offer 17:30 instead."})),
                      response(text("I can't change it myself, but here's a revision you can use.")))
    result = ask(state, "A1", "Please present a package with 17:30", client=good)
    assert result["suggestion"] == {"change_id": change["id"], "instructions": "Offer 17:30 instead."}

    bad = FakeClaude(response(tool_use("suggest_revision", {"change_id": "chg-001", "instructions": "x"})),
                     response(text("Sorry.")))
    result = ask(state, "A1", "Revise the Client C change", client=bad)
    assert result["suggestion"] is None
    assert bad.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_earlier_turns_are_sent_back(state):
    claude = FakeClaude(response(text("Yes.")))
    ask(state, "A1", "And at 17:45?", history=[{"question": "Would 17:30 work?", "answer": "Yes, 13 h 43 min."}],
        client=claude)
    roles = [m["role"] for m in claude.requests[0]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert claude.requests[0]["messages"][0]["content"].startswith("<trip_context>")


def test_an_empty_question_is_refused(state):
    with pytest.raises(ValueError):
        ask(state, "A1", "   ", client=FakeClaude())


def test_history_says_which_checks_earlier_answers_ran(state):
    claude = FakeClaude(response(text("Yes.")))
    ask(state, "A1", "And at 17:45?", client=claude,
        history=[{"question": "Would 17:30 work?", "answer": "Yes.", "tools": ["check_crew_duty"]}])
    assert claude.requests[0]["messages"][1]["content"].endswith("[Answered after running: check_crew_duty]")
