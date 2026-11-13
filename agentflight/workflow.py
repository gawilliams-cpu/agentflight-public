"""The change workflow: receive a message, then approve or reject what the agent proposed.

These are the service functions the CLI, the web page (step 5), and the eval
runner call. They work on a state dict in memory; workspace.py saves it.

  receive()  runs intake, files a change record in the queue, and makes the only
             sends that need no approval (the sales routing note, and
             informational updates when auto-send is on).
  approve()  re-checks the approved record updates against the current state,
             then applies them and dispatches the approved messages. If the
             re-check fails, nothing is executed.
  reject()   executes nothing and asks the trip manager for next steps.
"""

import json
from datetime import timedelta

from agentflight.agent.package import charged_total, price_fees
from agentflight.dispatch import DispatchRefused, dispatch
from agentflight.intake import intake
from agentflight.store import leg_by_id, now, trip_by_id
from agentflight.times import parse
from agentflight.tools.fixes import checks_for_changes
from agentflight.whatif import apply_changes

UPDATES_ITEM = "updates"


class WorkflowError(Exception):
    """An action that doesn't fit the change's current state (e.g. approving it twice)."""


# --- Receive ---------------------------------------------------------------

def receive(state: dict, source: str, sender: str, subject: str, body: str,
            client=None, on_event=None) -> tuple[dict, dict]:
    """Process one inbound message start to finish. Returns (change record, run record)."""
    change = start_change(state, source, sender, subject)

    def track(event):
        note_step(change, event)
        if on_event:
            on_event(event)

    record = intake(state, source, sender, subject, body, client=client, on_event=track)
    finish_change(state, change, record)
    return change, record


NOTE_CHANNELS = {"call": "Phone call", "text": "Text message"}


def note_contacts(state: dict) -> list:
    """Who a logged call or text can be from: client contacts and vendors, with an email address
    on file, so the agent can match the trip and reply in writing."""
    people = [{"email": c["email"], "name": c["name"], "group": client["company"]}
              for client in state["clients"] for c in client["contacts"]]
    people += [{"email": c["email"], "name": vendor["name"], "group": f"Vendors · {vendor['airport']}"}
               for vendor in state["vendors"] for c in vendor["contacts"]]
    return people


def prepare_note(state: dict, sender: str, channel: str, text: str) -> tuple[str, str]:
    """A call or text the trip manager logged, as (subject, body) for the one intake path.
    The note is still treated as untrusted data, like any inbound message."""
    if not text or not text.strip():
        raise WorkflowError("Write what was said, so the agent can act on it.")
    if channel not in NOTE_CHANNELS:
        raise WorkflowError(f"Unknown channel {channel!r}")
    person = next((p for p in note_contacts(state) if p["email"].lower() == sender.strip().lower()), None)
    if person is None:
        raise WorkflowError("Choose who it's from (a client contact or vendor on file).")
    subject = f"{NOTE_CHANNELS[channel]} with {person['name']} (logged by the trip manager)"
    body = (f"[{NOTE_CHANNELS[channel]} logged by the trip manager. The trip manager's note of what "
            f"{person['name']} said:]\n\n{text.strip()}")
    return subject, body


def note_record(channel: str, text: str) -> dict:
    """What the change card shows for a logged call or text: the channel and the trip manager's own words."""
    return {"channel": NOTE_CHANNELS[channel], "text": text.strip()}


def start_change(state: dict, source: str, sender: str, subject: str) -> dict:
    """File the change the moment a message arrives, as Processing, before the agent knows the trip."""
    change = {
        "id": f"chg-{len(state['queue']) + 1:03d}",
        "trip": None,
        "status": "Processing",
        "reason": None,
        "summary": f"New {source} from {sender}: {subject}",
        "created": state["clock"]["now"],
        "email": {"from": sender, "subject": subject},
        "step": "Reading the note" if source == "note" else "Reading the email",
        "package": None,
        "items": [],
        "awaiting_services": [],
        "history": [],
    }
    state["queue"].append(change)
    return change


def note_step(change: dict, event: dict) -> bool:
    """Keep a processing change's live step and trip current. Returns True if its trip changed."""
    if event.get("kind") != "step":
        return False
    change["step"] = event["text"]
    trip_id = (event.get("input") or {}).get("trip_id") if event.get("tool") == "get_trip" else None
    if trip_id and trip_id != change["trip"]:
        change["trip"] = trip_id
        return True
    return False


def finish_change(state: dict, change: dict, record: dict) -> dict:
    """Fill in the change from the finished run, then make the sends that need no approval."""
    package = record["package"]
    agent = record["agent"]
    if change.get("revises"):
        _guard_repeat(state, change["revises"], package)
    change.update({
        "trip": _trip_of(package),
        "status": package["status"],
        "reason": package["reason"],
        "summary": _summary_of(package),
        "email": record["email"],
        "run_id": record["run_id"],
        "package": package,
        "items": _items(package),
        "agent": {
            "events": [{k: e[k] for k in ("kind", "text", "ok") if k in e} for e in agent["events"]],
            "seconds": agent["seconds"], "cost_usd": agent["cost_usd"], "model": agent["model"],
            "prompt_version": agent["prompt_version"], "tools_version": agent["tools_version"],
        },
    })
    change.pop("step", None)
    record["sent"] = _automatic_sends(state, change)
    return change


def _proposal_fingerprint(package):
    """What a package would actually do: its record updates and charges, and each option's."""
    def canonical(items):
        return sorted(json.dumps(item, sort_keys=True) for item in items)

    cp = package.get("change_package") or {}
    options = (package.get("clarification") or {}).get("options", [])
    updates = canonical(cp.get("record_updates", [])) + canonical(cp.get("fees", []))
    option_changes = sorted(json.dumps(canonical(o.get("changes", [])) + canonical(o.get("fees", [])))
                            for o in options)
    return updates, option_changes


def _guard_repeat(state, rejected_id, package):
    """A revision that would do exactly what the trip manager just rejected is never offered for approval."""
    rejected = change_by_id(state, rejected_id).get("package") or {}
    new_updates, new_options = _proposal_fingerprint(package)
    if not (new_updates or new_options):
        return
    if (new_updates, new_options) == _proposal_fingerprint(rejected):
        package["problems"].append(
            "The revision repeats the rejected proposal. Nothing new to approve: revise again with "
            "clearer instructions, or reject and close.")
        package["status"] = "Needs review"


def _items(package):
    """What the trip manager decides on: the record updates (all or nothing), and each message."""
    items = []
    change_package = package.get("change_package")
    if change_package:
        if change_package["record_updates"] or change_package["priced_fees"]:
            items.append({"id": UPDATES_ITEM, "kind": "record_updates",
                          "changes": change_package["record_updates"],
                          "fees": change_package.get("fees", []),
                          "fee_total": change_package["fee_total"],
                          "legs": change_package.get("legs", []), "decision": None})
        messages = list(change_package["messages"])
        if package.get("clarification"):   # never drop a drafted question to the client
            messages += package["clarification"]["messages"]
    elif package.get("clarification"):
        messages = package["clarification"]["messages"]
    else:
        messages = []
    for i, message in enumerate(messages, start=1):
        items.append({"id": f"message-{i}", "kind": "message", "message": message, "decision": None})
    return items


def _automatic_sends(state, change):
    sent = []
    routing = change["package"].get("sales_routing")
    for message in (routing or {}).get("messages", []):
        sent.append(dispatch(state, message, "sales_routing", change["id"], change["trip"]))
    for item in change["items"]:
        if item["kind"] == "message" and item["message"].get("informational"):
            try:
                sent.append(dispatch(state, item["message"], "auto_informational", change["id"], change["trip"]))
            except DispatchRefused as refusal:
                change["history"].append({"at": state["clock"]["now"], "event": "auto_send_held",
                                          "item": item["id"], "reason": str(refusal)})
                continue
            item["decision"] = "sent_automatically"
    return sent


# --- Approve and reject ----------------------------------------------------

def approve(state: dict, change_id: str, decisions: dict | None = None) -> dict:
    """Approve a change. decisions maps item id to {"action": "approve" | "reject" | "edit",
    "subject"?, "body"?}; items not listed are approved ("Approve all").

    Returns {"executed": bool, "problems": [...], "sent": [...]}.
    """
    change = change_by_id(state, change_id)
    if change["status"] != "Needs approval":
        raise WorkflowError(f"{change_id} is '{change['status']}: {change['reason']}', not waiting for approval.")
    decisions = decisions or {}
    pending = [i for i in change["items"] if i["decision"] is None]
    actions = {item["id"]: decisions.get(item["id"], {"action": "approve"}) for item in pending}
    unknown = set(decisions) - set(actions)
    if unknown:
        raise WorkflowError(f"No pending item(s) {', '.join(sorted(unknown))} on {change_id}.")

    updates = next((i for i in pending if i["id"] == UPDATES_ITEM), None)
    approving_updates = updates is not None and actions[UPDATES_ITEM]["action"] == "approve"
    if updates and not approving_updates and any(
            actions[i["id"]]["action"] != "reject" for i in pending if i["kind"] == "message"):
        raise WorkflowError("The messages describe the record update. Reject them too, or approve the update.")

    # 1. Re-check against the current state. If anything now fails, execute nothing.
    if approving_updates:
        problems = _recheck(state, updates)
        if problems:
            change["status"], change["reason"] = "Needs review", "Re-check failed"
            change["history"].append({"at": state["clock"]["now"], "event": "recheck_failed", "problems": problems})
            return {"executed": False, "problems": problems, "sent": []}

    # 2. Apply the record updates and record the charges.
    if approving_updates:
        proposed = apply_changes(state, updates["changes"])
        state["schedule"], state["trips"] = proposed["schedule"], proposed["trips"]
        waived = [f for f in updates["fees"] if f.get("waived")]
        if updates["fee_total"] or waived:
            trip_by_id(state, change["trip"]).setdefault("charges", []).append(
                {"change": change_id, "amount": updates["fee_total"], "fees": updates["fees"],
                 "waived": waived})
        updates["decision"] = "approved"
        _close_answered_options(state, change, updates["changes"])
    elif updates:
        updates["decision"] = "rejected"

    # 3. Dispatch the approved messages, edited text where the trip manager edited it.
    sent = []
    for item in [i for i in pending if i["kind"] == "message"]:
        action = actions[item["id"]]
        if action["action"] == "reject":
            item["decision"] = "rejected"
            continue
        message = item["message"]
        if action["action"] == "edit":
            edited = {**message, "subject": action.get("subject", message["subject"]),
                      "body": action.get("body", message["body"])}
            change["history"].append({"at": state["clock"]["now"], "event": "edited", "item": item["id"],
                                      "original": {"subject": message["subject"], "body": message["body"]},
                                      "edited": {"subject": edited["subject"], "body": edited["body"]}})
            message = edited
        sent.append(dispatch(state, message, "approved", change_id, change["trip"]))
        item["decision"] = "edited" if action["action"] == "edit" else "approved"
        _await_confirmation(state, change, message)

    # 4. Status.
    if change["package"].get("clarification") and sent:
        change["status"], change["reason"] = "Waiting", "On client"
    elif change["awaiting_services"]:
        change["status"], change["reason"] = "Waiting", "On vendor confirmations"
    else:
        change["status"], change["reason"] = "Done", None
    change["history"].append({"at": state["clock"]["now"], "event": "approved", "sent": [e["id"] for e in sent]})
    refresh_waiting(state)
    return {"executed": True, "problems": [], "sent": sent}


def _close_answered_options(state, change, approved_changes):
    """The client's reply to an options email was approved: if the record updates make exactly the
    flight changes of one option we offered on this trip, that options request is answered and closes.
    Service updates that follow from the choice (e.g. a new catering delivery time) don't count."""
    def canonical(changes):
        return sorted(json.dumps(c, sort_keys=True) for c in changes if c.get("op") != "update_service")

    for waiting in state["queue"]:
        if (waiting is change or waiting.get("trip") != change["trip"]
                or (waiting.get("status"), waiting.get("reason")) != ("Waiting", "On client")):
            continue
        options = ((waiting.get("package") or {}).get("clarification") or {}).get("options", [])
        chosen = next((o for o in options if canonical(o.get("changes", [])) == canonical(approved_changes)), None)
        if chosen:
            waiting["status"], waiting["reason"] = "Done", f"Client chose option {chosen['label']} ({change['id']})"
            waiting["history"].append({"at": state["clock"]["now"], "event": "answered",
                                       "option": chosen["label"], "by": change["id"]})


def reject(state: dict, change_id: str, note: str | None = None) -> dict:
    """Reject and close: nothing is executed, and the change is closed with the reason recorded
    (the client withdrew, or the trip manager handled it another way)."""
    change = _close_rejected(state, change_id, "Rejected and closed", note)
    return change


def start_revision(state: dict, change_id: str, instructions: str) -> tuple[dict, dict]:
    """Reject a package and ask the agent for a new one, following the trip manager's instructions.
    Closes the old change and files the new one as Processing. Returns (new change, revision input);
    then run intake with source "revision" and finish_change, as for any message."""
    if not instructions or not instructions.strip():
        raise WorkflowError("Say what should change, so the agent can revise it.")
    old = change_by_id(state, change_id)
    if old["status"] not in ("Needs approval", "Needs review"):
        raise WorkflowError(f"{change_id} is not waiting for a decision.")
    email = old.get("email") or {}
    if not email.get("body"):
        raise WorkflowError(f"{change_id} has no original email to revise from.")
    new = start_change(state, "revision", email["from"], email["subject"])
    new["trip"] = old.get("trip")
    new["revises"] = change_id
    if old.get("note"):   # a revision of a logged call is still about that call
        new["note"] = old["note"]
    _close_rejected(state, change_id, f"Rejected; revised in {new['id']}", instructions)
    revision = {"instructions": instructions.strip(), "previous_summary": old.get("summary", "")}
    return new, revision


def revise(state: dict, change_id: str, instructions: str, client=None, on_event=None) -> tuple[dict, dict]:
    """start_revision, then run the agent and finish the new change, all in one call."""
    new, revision = start_revision(state, change_id, instructions)
    email = change_by_id(state, change_id)["email"]

    def track(event):
        note_step(new, event)
        if on_event:
            on_event(event)

    record = intake(state, "revision", email["from"], email["subject"], email["body"],
                    client=client, on_event=track, revision=revision)
    finish_change(state, new, record)
    return new, record


def _close_rejected(state, change_id, reason, note):
    change = change_by_id(state, change_id)
    if change["status"] not in ("Needs approval", "Needs review"):
        raise WorkflowError(f"{change_id} is not waiting for a decision.")
    for item in change["items"]:
        if item["decision"] is None:
            item["decision"] = "rejected"
    change["status"], change["reason"] = "Done", reason
    change["history"].append({"at": state["clock"]["now"], "event": "rejected", "note": note})
    return change


def _recheck(state, updates):
    problems = []
    try:
        results = checks_for_changes(state, updates["changes"])
    except ValueError as error:
        return [f"The update can no longer be applied: {error}"]
    problems += [r["summary"].splitlines()[0] for r in results if r["result"] == "fail"]
    priced = price_fees(state, updates["fees"], problems, waivers_allowed=True)  # waivers were checked when drafted
    total = charged_total(priced)
    if total != updates["fee_total"]:
        problems.append(f"Charges changed since the package was drafted: ${total:,} now, "
                        f"${updates['fee_total']:,} then.")
    return problems


# --- Readiness ---------------------------------------------------------------

def _await_confirmation(state, change, message):
    """A vendor that was sent a change must reconfirm its services on this trip."""
    address = message["to"].strip().lower()
    vendor_ids = {v["id"] for v in state["vendors"]
                  if any(c["email"].lower() == address for c in v["contacts"])}
    if not vendor_ids or not change["trip"]:
        return
    for service in trip_by_id(state, change["trip"])["services"]:
        if service["vendor"] in vendor_ids:
            service["status"] = "requested"
            if service["id"] not in change["awaiting_services"]:
                change["awaiting_services"].append(service["id"])


def refresh_waiting(state: dict) -> None:
    """Changes waiting on vendor confirmations become Done once every service they asked for is confirmed."""
    statuses = {s["id"]: s["status"] for trip in state["trips"] for s in trip["services"]}
    for change in state["queue"]:
        if change.get("status") == "Waiting" and change.get("reason") == "On vendor confirmations":
            awaiting = change.get("awaiting_services", [])
            if awaiting and all(statuses.get(sid) == "confirmed" for sid in awaiting):
                change["status"], change["reason"] = "Done", None


def readiness_flags(state: dict) -> list:
    """Services not yet confirmed when their first leg is within the readiness threshold."""
    threshold = timedelta(hours=state["policies"]["readiness_threshold_hours"])
    current = now(state)
    flags = []
    for trip in state["trips"]:
        for service in trip["services"]:
            if service["status"] == "confirmed":
                continue
            departure = min(parse(leg_by_id(state, leg_id)["departure"]) for leg_id in service["legs"])
            if current <= departure <= current + threshold:
                flags.append({"trip": trip["id"], "service": service["id"], "vendor": service["vendor"],
                              "status": service["status"], "departs": leg_by_id(state, service["legs"][0])["departure"],
                              "text": f"{trip['id']}: {service['type']} at {service['airport']} is "
                                      f"{service['status']}, not confirmed, and its leg departs within "
                                      f"{state['policies']['readiness_threshold_hours']} h."})
    return flags


# --- Lookups -----------------------------------------------------------------

def change_by_id(state, change_id):
    for change in state["queue"]:
        if change["id"] == change_id:
            return change
    raise WorkflowError(f"Unknown change: {change_id}")


def _trip_of(package):
    for part in ("change_package", "clarification", "sales_routing"):
        if package.get(part) and package[part].get("trip_id"):
            return package[part]["trip_id"]
    for flag in package.get("flags", []):
        if flag.get("trip_id"):
            return flag["trip_id"]
    return None


def _summary_of(package):
    for part in ("change_package", "clarification", "sales_routing"):
        if package.get(part):
            return package[part]["summary"]
    if package.get("flags"):
        return package["flags"][0]["reason"]
    return "; ".join(package.get("problems", [])) or "No proposal."
