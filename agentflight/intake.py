"""The one intake path. Real email, the demo control panel, and the eval runner
all deliver messages through intake(). Nothing here sends anything or changes data.
"""

import json
import traceback
from datetime import datetime

from agentflight import config
from agentflight.agent.loop import run_agent
from agentflight.agent.package import build_package
from agentflight.store import now
from agentflight.times import label


SOURCES = ("email", "note", "revision")
TAGS = ("inbound_email", "previous_proposal", "trip_manager_instructions")


def intake(state: dict, source: str, sender: str, subject: str, body: str,
           to: str | None = None, client=None, on_event=None, revision: dict | None = None) -> dict:
    """Process one inbound message and return the run record: package, trace, and cost.

    source "email": a message from the ops inbox.
    source "note": a phone call or text the trip manager logged, in their words. It goes to
    the agent like an email from the person who called (see workflow.prepare_note).
    source "revision": the same email again, after the trip manager rejected the
    first proposal; revision = {"instructions", "previous_summary"}.
    """
    if source not in SOURCES:
        raise ValueError(f"Unknown intake source {source!r}")
    if source == "revision" and not (revision and revision.get("instructions", "").strip()):
        raise ValueError("A revision needs the trip manager's instructions")

    received = state["clock"]["now"]
    email = {"from": sender, "to": to or state["operator"]["ops_email"],
             "subject": subject, "received": received, "body": body}
    agent_run = run_agent(state, _agent_input(state, email, revision), client=client, on_event=on_event)
    record = {
        "run_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "source": source,
        "email": email,
        "revision": revision,
        "agent": agent_run,
        "sent": [],  # nothing is ever sent at intake
    }
    try:
        record["package"] = build_package(state, agent_run, waivers_allowed=source == "revision")
    except Exception as error:  # keep the (paid-for) run, and show why it failed
        record["error"] = traceback.format_exc()
        record["package"] = {
            "status": "Needs review", "reason": "Run stopped",
            "problems": [f"Building the package failed: {error!r}"],
            "change_package": None, "clarification": None, "sales_routing": None,
            "flags": [], "checks_run": [],
        }
    return record


def _agent_input(state: dict, email: dict, revision: dict | None = None) -> str:
    """The user message: the current time, the email wrapped as untrusted data, and for a
    revision, the previous proposal and the trip manager's instructions."""
    def clean(text):
        # Stop any text from closing a wrapper early and posing as something else.
        for tag in TAGS:
            text = text.replace(f"<{tag}>", f"[{tag}]").replace(f"</{tag}>", f"[/{tag}]")
        return text

    message = (
        f"Current time: {label(now(state))} (US Eastern), {now(state):%Y-%m-%d}.\n\n"
        "<inbound_email>\n"
        f"From: {clean(email['from'])}\n"
        f"To: {clean(email['to'])}\n"
        f"Subject: {clean(email['subject'])}\n"
        f"Received: {email['received']}\n\n"
        f"{clean(email['body'])}\n"
        "</inbound_email>"
    )
    if revision:
        message += (
            "\n\n<previous_proposal>\n"
            f"{clean(revision.get('previous_summary', ''))}\n"
            "</previous_proposal>\n\n"
            "<trip_manager_instructions>\n"
            f"{clean(revision['instructions'])}\n"
            "</trip_manager_instructions>"
        )
    return message


def save_run(record: dict, name: str) -> str:
    """Save a run record under runs/ and return the file path."""
    config.RUNS_DIR.mkdir(exist_ok=True)
    path = config.RUNS_DIR / f"{record['run_id']}-{name}.json"
    with open(path, "w") as f:
        json.dump(record, f, indent=2, default=str)
    return str(path)
