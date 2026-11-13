"""Turn what the agent proposed into a change package, and verify it in code.

The agent's word is not enough. Before the trip manager sees anything, this
module:
- re-runs the checks on every proposed record update and every option,
- prices every charge from the rate card (the agent names charges; code prices them),
- confirms every recipient is a known address, and a client recipient is this trip's client,
- confirms every dollar amount in a drafted message matches a priced charge.
Anything that fails is listed under "problems" and the package needs review.
"""

import re

from agentflight.store import trip_by_id
from agentflight.tools.fees import compute_change_fee
from agentflight.tools.fixes import checks_for_changes
from agentflight.tools.flight import changed_leg_times

DOLLARS = re.compile(r"\$\s?(\d[\d,]*)(?:\.(\d{2}))?")


def build_package(state: dict, agent_run: dict, waivers_allowed: bool = False) -> dict:
    """waivers_allowed: True only when the run carries the trip manager's instructions (a revision).
    A waiver anywhere else (for example, a client asking in an email) is refused."""
    package = {
        "change_package": None,
        "clarification": None,
        "sales_routing": None,
        "flags": [],
        "problems": [],
    }
    if agent_run["stopped"]:
        package["problems"].append(agent_run["stopped"])

    # If the agent re-proposed (to correct itself), only its latest proposal of each kind counts,
    # and only that one is verified. Flags all count.
    latest = {}
    for output in agent_run["outputs"]:
        if output["tool"] != "flag_message":
            latest[output["tool"]] = output
    outputs = [o for o in agent_run["outputs"] if o["tool"] == "flag_message" or latest[o["tool"]] is o]

    for output in outputs:
        tool, proposal = output["tool"], output["input"]
        if tool == "propose_change_package":
            package["change_package"] = _verify_change_package(state, proposal, package["problems"], waivers_allowed)
        elif tool == "propose_clarification":
            package["clarification"] = _verify_clarification(state, proposal, package["problems"], waivers_allowed)
        elif tool == "route_to_sales":
            package["sales_routing"] = _verify_sales_routing(state, proposal, package["problems"])
        elif tool == "flag_message":
            package["flags"].append(proposal)

    package["status"], package["reason"] = _status(package)
    package["checks_run"] = [step for step in agent_run["trace"] if "result" in step
                             and isinstance(step["result"], dict) and "check" in step["result"]]
    return package


def _verify_change_package(state, proposal, problems, waivers_allowed=False):
    trip_id = proposal.get("trip_id")
    updates = proposal.get("record_updates", [])
    verified_checks = verify_changes(state, updates, problems, "Proposed update")
    priced = price_fees(state, proposal.get("fees", []), problems, waivers_allowed)
    allowed = _allowed_amounts(priced)
    for message in proposal.get("messages", []):
        _check_message(state, trip_id, message, allowed, problems)
    return {**proposal, "legs": _legs(state, updates), "verified_checks": verified_checks,
            "priced_fees": priced, "fee_total": charged_total(priced)}


def _verify_clarification(state, proposal, problems, waivers_allowed=False):
    trip_id = proposal.get("trip_id")
    options, allowed = [], set()
    for option in proposal.get("options", []):
        label = f"Option {option.get('label', '?')}"
        verified_checks = verify_changes(state, option.get("changes", []), problems, label)
        priced = price_fees(state, option.get("fees", []), problems, waivers_allowed)
        allowed |= _allowed_amounts(priced)
        options.append({**option, "legs": _legs(state, option.get("changes", [])),
                        "verified_checks": verified_checks, "priced_fees": priced,
                        "fee_total": charged_total(priced),
                        "passes": all(c["result"] == "pass" for c in verified_checks)})
    for message in proposal.get("messages", []):
        _check_message(state, trip_id, message, allowed, problems)
    return {**proposal, "options": options}


def _verify_sales_routing(state, proposal, problems):
    """The agent writes the routing note; code addresses it to sales from the operator data."""
    sales = next((p for p in state["operator"]["people"] if p["role"] == "Charter sales"), None)
    if sales is None:
        problems.append("No Charter sales contact in the operator data; the routing note has no recipient.")
        return proposal
    messages = [{**note, "to": sales["email"], "recipient_role": "sales"}
                for note in proposal.get("messages", [])]
    for message in messages:
        _check_message(state, proposal.get("trip_id"), message, set(), problems)
    return {**proposal, "messages": messages}


def verify_changes(state, changes, problems, label):
    """Re-run the checks that apply to a set of changes. Returns the check results."""
    try:
        results = checks_for_changes(state, changes)
    except (ValueError, KeyError) as error:
        problems.append(f"{label}: the changes can't be applied ({error}).")
        return []
    for result in results:
        if result["result"] == "fail":
            problems.append(f"{label} fails a check: {result['summary']}")
    return results


def _legs(state, changes):
    """The changed legs' new times, for display. Empty if the changes can't be applied
    (verify_changes has already recorded that as a problem)."""
    try:
        return changed_leg_times(state, changes)
    except (ValueError, KeyError):
        return []


def price_fees(state, fees, problems, waivers_allowed=False):
    """Price each named charge from the rate card. A charge marked waived is still priced
    (so the waiver is recorded with its amount) but not charged, and only the trip manager
    can waive one."""
    priced = []
    for fee in fees:
        try:
            result = compute_change_fee(state, fee["charge"], leg_ids=fee.get("leg_ids"),
                                        trip_id=fee.get("trip_id"), count=fee.get("count", 1))
        except (ValueError, KeyError) as error:
            problems.append(f"Charge {fee.get('charge')!r} can't be priced: {error}")
            continue
        if not result["defined"]:
            problems.append(result["summary"])
        result["waived"] = bool(fee.get("waived")) and result["defined"]
        if result["waived"] and not waivers_allowed:
            problems.append(f"{result['label']} is marked waived, but only the trip manager can waive "
                            f"a charge. It stays charged.")
            result["waived"] = False
        priced.append(result)
    return priced


def charged_total(priced):
    """What the client is actually charged: defined charges that weren't waived."""
    return sum(f["amount"] for f in priced if f["defined"] and not f.get("waived"))


def _allowed_amounts(priced):
    """Dollar amounts a message may mention: each charge, each line, and the total."""
    amounts = {f["amount"] for f in priced if f["defined"]}
    amounts |= {line["amount"] for f in priced if f["defined"] for line in f["breakdown"]}
    amounts.add(sum(f["amount"] for f in priced if f["defined"]))
    amounts.add(charged_total(priced))
    return amounts


def _check_message(state, trip_id, message, allowed_amounts, problems):
    address = message["to"].strip().lower()
    who = _known_address(state, address)
    if who is None:
        problems.append(f"Drafted message to {message['to']}: that address is not in the data.")
    elif who[0] == "client" and trip_id:
        try:
            trip_client = trip_by_id(state, trip_id)["client"]
        except ValueError:
            trip_client = None
        if who[1] != trip_client:
            problems.append(f"Drafted message to {message['to']}: not a contact of this trip's client.")

    for whole, cents in DOLLARS.findall(message["body"] + " " + message["subject"]):
        amount = int(whole.replace(",", ""))
        if cents and cents != "00":
            amount += int(cents) / 100
        if amount not in allowed_amounts:
            problems.append(f"Drafted message to {message['to']} mentions ${whole}, "
                            f"which doesn't match any charge priced from the rate card.")


def _known_address(state, address):
    """("client", client_id), ("vendor", id), ("crew", id), ("operator", None), or None."""
    for client in state["clients"]:
        if any(c["email"].lower() == address for c in client["contacts"]):
            return ("client", client["id"])
    for vendor in state["vendors"]:
        if any(c["email"].lower() == address for c in vendor["contacts"]):
            return ("vendor", vendor["id"])
    for crew in state["crew"]:
        if any(m["email"].lower() == address for m in crew["members"]):
            return ("crew", crew["id"])
    operator = state["operator"]
    if address == operator["ops_email"] or any(p["email"].lower() == address for p in operator["people"]):
        return ("operator", None)
    return None


def _status(package):
    """Needs approval: a clean package waiting on the trip manager's decision.
    Needs review: something is wrong or unresolved and a person must look first."""
    if package["change_package"]:
        status = ("Needs approval", "Change package")
    elif package["clarification"]:
        has_options = bool(package["clarification"].get("options"))
        status = ("Needs approval", "Options for client" if has_options else "Question for client")
    elif package["sales_routing"]:
        status = ("Waiting", "Routed to sales")
    elif package["flags"]:
        status = ("Needs review", "Flagged")
    else:
        status = ("Needs review", "Run stopped")
    if package["problems"] or package["flags"]:
        status = ("Needs review", status[1])
    return status
