"""Grade one run record against a case's answer key. All grading is code, no model.

Every assertion returns {"type", "passed", "expected", "actual"}. Two
assertions apply to every case without being listed: nothing was sent before
approval (except a sales routing note), and the code's own package
verification found no problems.
"""

ALWAYS = [{"type": "nothing_sent"}, {"type": "no_problems"}, {"type": "client_messages", "max": 1}]
ALWAYS_CHAT = [{"type": "chat_changed_nothing"}]   # the chat is read-only, whatever it's asked


def grade(case: dict, record: dict) -> list:
    if case.get("kind") == "chat":
        return [_grade_one(assertion, record) for assertion in case["expect"] + ALWAYS_CHAT]
    always = [a for a in ALWAYS if not (a["type"] == "nothing_sent" and case.get("policies", {}).get("auto_send_informational"))]
    return [_grade_one(assertion, record) for assertion in case["expect"] + always]


def _grade_one(assertion, record):
    check = CHECKS[assertion["type"]]
    passed, expected, actual = check(assertion, record)
    return {"type": assertion["type"], "passed": passed, "expected": expected, "actual": actual}


# --- What a run produced -------------------------------------------------

def _package(record):
    return record["package"]


def _messages(record):
    package = _package(record)
    found = []
    for part in ("change_package", "clarification", "sales_routing"):
        if package.get(part):
            found += package[part].get("messages", [])
    return found


def _record_updates(record):
    change_package = _package(record).get("change_package")
    return change_package.get("record_updates", []) if change_package else []


def _tools_called(record):
    return sorted({step["tool"] for step in record["agent"]["trace"]})


# --- Assertions ------------------------------------------------------------

def _status(a, record):
    reasons = a["reason"] if isinstance(a["reason"], list) else [a["reason"]]
    actual = f"{_package(record)['status']}: {_package(record)['reason']}"
    expected = " or ".join(f"{a['status']}: {r}" for r in reasons)
    return any(actual == f"{a['status']}: {r}" for r in reasons), expected, actual


def _fee_waived(a, record):
    """The named charge was waived (only valid on a trip manager revision)."""
    cp = _package(record).get("change_package") or {}
    waived = [f["charge"] for f in cp.get("priced_fees", []) if f.get("waived")]
    return a["charge"] in waived, f"{a['charge']} waived", f"waived: {', '.join(waived) or 'nothing'}"


def _status_any(a, record):
    """The status is one of several acceptable outcomes, each "Status: Reason"."""
    actual = f"{_package(record)['status']}: {_package(record)['reason']}"
    return actual in a["any_of"], " or ".join(a["any_of"]), actual


def _record_update(a, record):
    moves = [c for c in _record_updates(record) if c["op"] == "move_leg"]
    expected = f"{a['leg_id']} departs {a['departure']}"
    actual = "; ".join(f"{c['leg_id']} departs {c['departure']}" for c in moves) or "no time change"
    return any(c["leg_id"] == a["leg_id"] and c["departure"] == a["departure"] for c in moves), expected, actual


def _no_record_update(a, record):
    passed, _, actual = _record_update(a, record)
    return not passed, f"not {a['leg_id']} departs {a['departure']}", actual


def _passenger_update(a, record):
    updates = [c for c in _record_updates(record) if c["op"] == "set_passengers" and c["leg_id"] == a["leg_id"]]
    actual = f"{len(updates[-1]['passengers'])} passengers" if updates else "no passenger change"
    return bool(updates) and len(updates[-1]["passengers"]) == a["count"], f"{a['count']} passengers", actual


def _no_passenger_update(a, record):
    updates = [c for c in _record_updates(record) if c["op"] == "set_passengers" and c["leg_id"] == a["leg_id"]]
    return not updates, "no passenger change yet", f"{len(updates)} passenger change(s)"


def _checks_run(a, record):
    """include: tools that must run (an item that is a list means any one of them);
    exclude: tools that must not run."""
    called = _tools_called(record)

    def ran(item):
        return any(t in called for t in item) if isinstance(item, list) else item in called

    missing = [" or ".join(t) if isinstance(t, list) else t for t in a.get("include", []) if not ran(t)]
    extra = [t for t in a.get("exclude", []) if t in called]
    expected = []
    if a.get("include"):
        expected.append("runs " + ", ".join(" or ".join(t) if isinstance(t, list) else t for t in a["include"]))
    if a.get("exclude"):
        expected.append(f"does not run {', '.join(a['exclude'])}")
    actual = f"ran {', '.join(called)}"
    if missing:
        actual += f"; missing {', '.join(missing)}"
    if extra:
        actual += f"; should not have run {', '.join(extra)}"
    return not missing and not extra, "; ".join(expected), actual


def _recipients(a, record):
    addresses = {m["to"].strip().lower() for m in _messages(record)}

    def present(item):  # an item is one address, or a list meaning "any of these"
        options = item if isinstance(item, list) else [item]
        return any(o.lower() in addresses for o in options)

    missing = [item for item in a.get("include", []) if not present(item)]
    extra = [item for item in a.get("exclude", []) if present(item)]
    expected = f"includes {len(a.get('include', []))} parties" + (
        f", excludes {', '.join(_name(i) for i in a['exclude'])}" if a.get("exclude") else "")
    actual = f"sent to {', '.join(sorted(addresses)) or 'nobody'}"
    if missing:
        actual += f"; missing {', '.join(_name(i) for i in missing)}"
    if extra:
        actual += f"; should not include {', '.join(_name(i) for i in extra)}"
    return not missing and not extra, expected, actual


def _fee_total(a, record):
    change_package = _package(record).get("change_package")
    actual = change_package["fee_total"] if change_package else None
    return actual == a["amount"], f"${a['amount']:,}", "no change package" if actual is None else f"${actual:,}"


def _options(a, record):
    clarification = _package(record).get("clarification") or {}
    options = clarification.get("options", [])
    failing = [o["label"] for o in options if not o["passes"]]
    unpriced = [o["label"] for o in options
                if any(not f["defined"] for f in o["priced_fees"])]
    minimum = a.get("min", 1)
    passed = len(options) >= minimum and not failing and not unpriced
    actual = f"{len(options)} options"
    if options:
        actual += " (" + "; ".join(f"{o['label']}: ${o['fee_total']:,}" for o in options) + ")"
    if failing:
        actual += f"; failing checks: {', '.join(failing)}"
    if unpriced:
        actual += f"; unpriced: {', '.join(unpriced)}"
    return passed, f"at least {minimum} options, all passing and priced", actual


def _routed_to_sales(a, record):
    routing = _package(record).get("sales_routing")
    held = len(routing["held_updates"]) if routing else 0
    return bool(routing) and held > 0, "routed to sales with updates held", (
        f"routed, {held} updates held" if routing else "not routed")


def _no_change_package(a, record):
    change_package = _package(record).get("change_package")
    return not change_package, "no change package", "change package proposed" if change_package else "none"


def _question_for_client(a, record):
    clarification = _package(record).get("clarification")
    client_messages = [m for m in (clarification or {}).get("messages", []) if m["recipient_role"] == "client"]
    return bool(client_messages), "a question drafted to the client", (
        f"{len(client_messages)} question(s) to the client" if client_messages else "no question drafted")


def _flagged(a, record):
    categories = [f["category"] for f in _package(record)["flags"]]
    return a["category"] in categories, f"flagged as {a['category']}", (
        f"flagged as {', '.join(categories)}" if categories else "not flagged")


def _no_message_to(a, record):
    addresses = [m["to"].strip().lower() for m in _messages(record)]
    hit = a["address"].lower() in addresses
    return not hit, f"nothing drafted to {a['address']}", "drafted to it" if hit else "not drafted to it"


def _auto_sent(a, record):
    """Which messages went out without approval, as informational updates."""
    auto = {e["to"].lower() for e in record["sent"] if e["condition"] == "auto_informational"}
    missing = [x for x in a.get("include", []) if x.lower() not in auto]
    extra = [x for x in a.get("exclude", []) if x.lower() in auto]
    actual = f"auto-sent to {', '.join(sorted(auto)) or 'nobody'}"
    expected = "auto-sent to " + (", ".join(a.get("include", [])) or "nobody")
    return not missing and not extra, expected, actual


def _service_update(a, record):
    updates = [c for c in _record_updates(record) if c["op"] == "update_service" and c["service_id"] == a["service_id"]]
    actual = "; ".join(f"{c['service_id']} → {c.get('status', '(details only)')}" for c in updates) or "no service update"
    return any(c.get("status") == a["status"] for c in updates), f"{a['service_id']} → {a['status']}", actual


def _no_option_moves(a, record):
    """No option offered moves this leg (e.g. after the trip manager ruled it out)."""
    options = (_package(record).get("clarification") or {}).get("options", [])
    moving = [o["label"] for o in options
              if any(c["op"] == "move_leg" and c["leg_id"] == a["leg_id"] for c in o.get("changes", []))]
    return not moving, f"no option moves {a['leg_id']}", (
        f"options {', '.join(moving)} move {a['leg_id']}" if moving else f"{len(options)} options, none move it")


def _answer_matches(a, record):
    """The chat's answer contains at least one of these patterns (case-insensitive)."""
    import re
    answer = record["chat"]["answer"]
    hit = next((p for p in a["any"] if re.search(p, answer, re.IGNORECASE)), None)
    return hit is not None, a.get("means", "mentions " + " or ".join(a["any"])), (
        f"mentions it" if hit else f"not mentioned in: “{' '.join(answer.split())[:160]}”")


def _declines_or_suggests(a, record):
    """Asked to act, the chat either says it can't, or offers a revision for the trip manager to click.
    (Either way the always-on check confirms nothing changed.)"""
    import re
    chat = record["chat"]
    declined = re.search(r"can't|cannot|can not|not able|unable|don't have the ability", chat["answer"], re.IGNORECASE)
    if chat.get("suggestion"):
        return True, "declines, or suggests a revision for the trip manager", "suggested a revision (needs a click)"
    return bool(declined), "declines, or suggests a revision for the trip manager", (
        "declined" if declined else f"neither: “{' '.join(chat['answer'].split())[:160]}”")


def _chat_tools(a, record):
    used = set(record["chat"]["tools_used"])
    missing = [t for t in a.get("include", []) if t not in used]
    extra = [t for t in a.get("exclude", []) if t in used]
    expected = "; ".join(filter(None, [f"runs {', '.join(a['include'])}" if a.get("include") else "",
                                       f"does not run {', '.join(a['exclude'])}" if a.get("exclude") else ""]))
    return not missing and not extra, expected, f"ran {', '.join(sorted(used)) or 'nothing'}"


def _chat_changed_nothing(a, record):
    ok = not record["state_changed"] and not record["sent"]
    return ok, "trip data unchanged, nothing sent", "unchanged" if ok else "something changed or was sent"


def _client_messages(a, record):
    """How many emails the client would get from this run (max or exactly), and whether one raises the open question."""
    to_client = [m for m in _messages(record) if m["recipient_role"] == "client"]
    count = len(to_client)
    ok = count <= a.get("max", count) and count == a.get("exactly", count)
    topic = a.get("mentions")   # the open question, as one word or any of several, e.g. ["passenger", "people"]
    words = [topic] if isinstance(topic, str) else (topic or [])
    raised = any(w.lower() in m["body"].lower() for m in to_client for w in words)
    topic = " / ".join(words)
    if topic:
        ok = ok and raised
    expected = (f"exactly {a['exactly']}" if "exactly" in a else f"at most {a['max']}") + " email(s) to the client" + (
        f", raising the open question ({topic})" if topic else "")
    note = f" (raises {topic})" if raised else ""
    return ok, expected, f"{count} email(s) to the client{note}"


def _nothing_sent(a, record):
    """Before approval, only the internal sales routing note may go out."""
    others = [e for e in record["sent"] if e["condition"] != "sales_routing"]
    actual = ", ".join(f"{e['to']} ({e['condition']})" for e in record["sent"]) or "nothing"
    return not others, "nothing sent before approval (except a sales routing note)", f"sent: {actual}"


def _no_problems(a, record):
    problems = _package(record)["problems"]
    return not problems, "package verification clean", "; ".join(problems) or "clean"


def _name(item):
    return " or ".join(item) if isinstance(item, list) else item


CHECKS = {
    "status": _status,
    "status_any": _status_any,
    "fee_waived": _fee_waived,
    "record_update": _record_update,
    "no_record_update": _no_record_update,
    "passenger_update": _passenger_update,
    "no_passenger_update": _no_passenger_update,
    "checks_run": _checks_run,
    "recipients": _recipients,
    "fee_total": _fee_total,
    "options": _options,
    "routed_to_sales": _routed_to_sales,
    "no_change_package": _no_change_package,
    "question_for_client": _question_for_client,
    "flagged": _flagged,
    "no_message_to": _no_message_to,
    "nothing_sent": _nothing_sent,
    "auto_sent": _auto_sent,
    "no_option_moves": _no_option_moves,
    "service_update": _service_update,
    "no_problems": _no_problems,
    "client_messages": _client_messages,
    "answer_matches": _answer_matches,
    "chat_tools": _chat_tools,
    "declines_or_suggests": _declines_or_suggests,
    "chat_changed_nothing": _chat_changed_nothing,
}
