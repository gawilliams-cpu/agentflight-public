"""The one dispatch function. Every message that leaves AgentFlight goes through here.

It sends (to the mock outbox; never real email) only under one of three conditions:
  approved       the trip manager approved this item
  sales_routing  the internal routing note to sales
  auto_informational
                 an informational update to a party already on the trip, while the
                 operator's auto-send policy is on
Anything else is refused. Every send records the condition that allowed it.
"""

import re
from datetime import datetime

from agentflight.store import trip_by_id

CONDITIONS = ("approved", "sales_routing", "auto_informational")
DOLLARS = re.compile(r"\$\s?\d")


class DispatchRefused(Exception):
    """A send that no condition allows. Nothing was sent."""


def dispatch(state: dict, message: dict, condition: str, change_id: str,
             trip_id: str | None = None) -> dict:
    """Put one message in the mock outbox, if the condition allows it. Returns the outbox entry."""
    if condition not in CONDITIONS:
        raise DispatchRefused(f"Unknown send condition: {condition!r}")
    if condition == "sales_routing":
        sales = [p["email"] for p in state["operator"]["people"] if p["role"] == "Charter sales"]
        if message["to"] not in sales:
            raise DispatchRefused("A sales routing note can only go to sales.")
    if condition == "auto_informational":
        reason = auto_send_refusal(state, message, trip_id)
        if reason:
            raise DispatchRefused(reason)

    entry = {
        "id": f"msg-{len(state['outbox']) + 1:03d}",
        "change": change_id,
        "trip": trip_id,
        "to": message["to"],
        "recipient_role": message["recipient_role"],
        "subject": message["subject"],
        "body": message["body"],
        "condition": condition,
        "sent_at": state["clock"]["now"],
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    }
    state["outbox"].append(entry)
    return entry


def auto_send_refusal(state: dict, message: dict, trip_id: str | None) -> str | None:
    """Why this message may not go out without approval, or None if it may."""
    if not state["policies"].get("auto_send_informational"):
        return "Auto-send is off in the operator's policy."
    if not message.get("informational"):
        return "The message is not marked informational."
    if message["recipient_role"] == "client":
        return "Messages to the client always need approval."
    if DOLLARS.search(message["body"] + message["subject"]):
        return "Messages that mention a price always need approval."
    if not trip_id or message["to"].strip().lower() not in parties_on_trip(state, trip_id):
        return "The recipient is not already a party to this trip."
    return None


def parties_on_trip(state: dict, trip_id: str) -> set:
    """Vendor and crew addresses already involved in a trip."""
    trip = trip_by_id(state, trip_id)
    vendors = {s["vendor"] for s in trip["services"]}
    crews = {leg["crew"] for leg in state["schedule"]["legs"] if leg["trip"] == trip_id}
    addresses = set()
    for vendor in state["vendors"]:
        if vendor["id"] in vendors:
            addresses |= {c["email"].lower() for c in vendor["contacts"]}
    for crew in state["crew"]:
        if crew["id"] in crews:
            addresses |= {m["email"].lower() for m in crew["members"]}
    return addresses
