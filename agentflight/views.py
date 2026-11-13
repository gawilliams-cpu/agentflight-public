"""What each screen shows, built from the state. The page only displays these.

overview()   the Trips list (home and work queue) and the header
trip_view()  one trip: decisions, in progress, the trip record, history
reference()  the Reference view: aircraft, pilots, airports and vendors, clients
schedule_board()  the schedule board: the week's flights by aircraft and by pilot

All judgment about what needs attention, urgency, and wording lives here, so the
live page and the exported sample-mode page show exactly the same thing.
"""

import json
from difflib import SequenceMatcher
from pathlib import Path

from agentflight.store import (DATA_DIR, aircraft_by_tail, client_by_id, crew_by_id, leg_by_id, now, service_by_id,
                               trip_by_id, vendor_by_id)
from agentflight.times import day_label, label, minutes_between, parse
from agentflight.tools.flight import leg_times
from agentflight.whatif import apply_changes
from agentflight.tools.reference import find_trips_for_sender, get_trip
from agentflight.workflow import readiness_flags

DECISION = ("Needs approval", "Needs review")
URGENCY = {"Processing": 0, "Needs review": 1, "Needs approval": 2, "Readiness": 3, "Waiting": 4, "Done": 9}
TONE = {"Processing": "working", "Needs review": "problem", "Needs approval": "decide",
        "Readiness": "problem", "Waiting": "waiting", "Done": "done"}


# --- Trips list ----------------------------------------------------------------

def overview(state: dict) -> dict:
    current = now(state)
    flags = readiness_flags(state)
    rows = [_trip_row(state, trip, flags, current) for trip in state["trips"]]
    rows += [_unmatched_row(state, change) for change in state["queue"]
             if change.get("trip") is None and change["status"] in DECISION]
    rows.sort(key=lambda r: (r["urgency"], r["first_departure"] or ""))
    processing = [{"change": c["id"], "from": c["email"]["from"], "subject": c["email"]["subject"],
                   "step": c.get("step", ""), "trip": c.get("trip")}
                  for c in state["queue"] if c["status"] == "Processing"]
    return {
        "now": state["clock"]["now"],
        "now_label": label(current),
        "operator": state["operator"]["name"],
        "attention_count": sum(r["attention"] for r in rows) + sum(
            1 for c in state["queue"] if c["status"] == "Processing" and c.get("trip") is None),
        "processing": processing,
        "rows": rows,
    }


def _trip_row(state, trip, flags, current):
    legs = [leg_by_id(state, entry["leg"]) for entry in trip["legs"]]
    first = min(parse(leg["departure"]) for leg in legs)
    last_arrival = max(leg_times(state, leg)[1] for leg in legs)
    client = client_by_id(state, trip["client"])
    aircraft = aircraft_by_tail(state, legs[0]["aircraft"])
    changes = [c for c in state["queue"] if c.get("trip") == trip["id"] and c["status"] != "Done"]
    trip_flags = [f for f in flags if f["trip"] == trip["id"]]

    status = _row_status(changes, trip_flags)
    return {
        "kind": "trip",
        "trip": trip["id"],
        "title": trip["title"],
        "client": client["company"],
        "route": "–".join([legs[0]["origin"]] + [leg["destination"] for leg in legs]),
        "first_departure": first.isoformat(timespec="minutes"),
        "departure_label": label(first),
        "countdown": "completed" if last_arrival < current else _countdown(current, first),
        "hours_to_departure": round(minutes_between(current, first) / 60, 1),
        "completed": last_arrival < current,
        "aircraft": f"{aircraft['tail']} {aircraft['type_name']}",
        "status": status["label"],
        "tone": status["tone"],
        "step": status.get("step"),
        "attention": status["attention"],
        "urgency": status["urgency"],
        "flags": [f["text"] for f in trip_flags],
    }


def _row_status(changes, trip_flags):
    """The single most urgent thing about a trip, for its row."""
    working = [c for c in changes if c["status"] == "Processing"]
    if working:
        return {"label": "Agent working", "tone": TONE["Processing"], "step": working[0].get("step", ""),
                "attention": True, "urgency": URGENCY["Processing"]}
    for status in DECISION:
        matching = [c for c in changes if c["status"] == status]
        if matching:
            text = f"{status}: {matching[0]['reason']}"
            if len(matching) > 1:
                text = f"{status}: {len(matching)} changes"
            return {"label": text, "tone": TONE[status], "attention": True, "urgency": URGENCY[status]}
    if trip_flags:
        return {"label": "Unconfirmed vendor near departure", "tone": TONE["Readiness"],
                "attention": True, "urgency": URGENCY["Readiness"]}
    waiting = [c for c in changes if c["status"] == "Waiting"]
    if waiting:
        return {"label": f"Waiting: {waiting[0]['reason']}", "tone": TONE["Waiting"],
                "attention": False, "urgency": URGENCY["Waiting"]}
    return {"label": "No open changes", "tone": TONE["Done"], "attention": False, "urgency": URGENCY["Done"]}


def _unmatched_row(state, change):
    """A request the agent couldn't tie to one trip: the only non-trip row."""
    sender = find_trips_for_sender(state, change["email"]["from"])
    company = sender["sender"].get("company") or change["email"]["from"]
    options = ", ".join(f"{t['trip']} ({t['title']})" for t in sender["upcoming_trips"])
    return {
        "kind": "unmatched", "trip": None, "change": change["id"], "title": "Trip unclear",
        "client": company, "route": options, "first_departure": None, "departure_label": "",
        "countdown": "", "hours_to_departure": None, "completed": False, "aircraft": "",
        "status": f"{change['status']}: {change['reason']}", "tone": TONE[change["status"]],
        "step": None, "attention": True, "urgency": URGENCY[change["status"]], "flags": [],
    }


def _countdown(current, departure):
    minutes = minutes_between(current, departure)
    if minutes < 0:
        return "underway"
    days, rest = divmod(minutes, 24 * 60)
    hours = rest // 60
    return f"in {days} d {hours} h" if days else f"in {hours} h {rest % 60:02d} min"


# --- Trip page -----------------------------------------------------------------

def trip_view(state: dict, trip_id: str) -> dict:
    record = get_trip(state, trip_id)
    trip = trip_by_id(state, trip_id)
    changes = [c for c in state["queue"] if c.get("trip") == trip_id]
    decisions = sorted([c for c in changes if c["status"] in DECISION], key=lambda c: c["id"])
    in_progress = [c for c in changes if c["status"] in ("Processing", "Waiting")]
    history = [c for c in changes if c["status"] == "Done"]

    decision_cards = []
    for index, change in enumerate(decisions, start=1):
        card = change_card(state, change)
        card["number"] = index
        earlier = [d for d in decisions[:index - 1]]
        card["drafted_before"] = [f"change {i}" for i, _ in enumerate(earlier, start=1)]
        decision_cards.append(card)

    proposed = {}
    for card in decision_cards:
        for leg in card["legs"]:
            proposed.setdefault(leg["leg"], []).append({**leg, "number": card["number"]})
    for leg in record["legs"]:
        leg["proposed"] = proposed.get(leg["leg"], [])
        leg["departure_label"] = label(parse(leg["departure"]))
        leg["arrival_time"] = leg["arrival"][-5:]
        # Each leg carries its own services; one that spans legs (e.g. handling at a
        # stop between two legs) appears under each, noting the other legs it covers.
        leg["services"] = [{**service, "also_legs": [l for l in service["legs"] if l != leg["leg"]]}
                           for service in record["services"] if leg["leg"] in service["legs"]]

    current = now(state)
    first = min(parse(leg["departure"]) for leg in record["legs"])
    return {
        "trip": trip_id,
        "title": record["title"],
        "client": record["client"]["company"],
        "client_id": record["client"]["id"],
        "contacts": record["client"]["contacts"],
        "departure_label": label(first),
        "countdown": _countdown(current, first),
        "aircraft": record["legs"][0]["aircraft"],
        "crew": sorted({(leg["crew"]["name"]) for leg in record["legs"]}),
        "legs": record["legs"],
        "services": record["services"],
        "charges": trip.get("charges", []),
        "notes": record["notes"],
        "decisions": decision_cards,
        "in_progress": [change_card(state, c) for c in in_progress],
        "history": [change_card(state, c) for c in history],
        "flags": [f["text"] for f in readiness_flags(state) if f["trip"] == trip_id],
        "now_label": label(current),
    }


def change_card(state: dict, change: dict) -> dict:
    """One change, as its card shows it: compact facts first, details available."""
    package = change.get("package") or {}
    cp = package.get("change_package") or {}
    clar = package.get("clarification") or {}
    checks = cp.get("verified_checks", [])
    sent = [e for e in state["outbox"] if e["change"] == change["id"]]
    return {
        "id": change["id"],
        "status": change["status"],
        "reason": change.get("reason"),
        "tone": TONE.get(change["status"], "waiting"),
        "summary": change["summary"],
        "created": change["created"],
        "email": change.get("email", {}),
        "request": _request(state, change),
        "from_name": recipient_name(state, change["email"]["from"]).split(" (")[0] if change.get("email") else "",
        "step": change.get("step"),
        "legs": cp.get("legs", []),
        "changes": describe_changes(state, cp.get("record_updates", [])),
        "checks": named_checks(checks),
        "fees": cp.get("priced_fees", []),
        "fee_total": cp.get("fee_total", 0),
        "options": [{**o, "changes_described": describe_changes(state, o.get("changes", [])),
                     "checks": named_checks(o.get("verified_checks", []))}
                    for o in clar.get("options", [])],
        "sales_routing": package.get("sales_routing"),
        "checks_run": named_checks([step["result"] for step in package.get("checks_run", [])]),
        "flags": package.get("flags", []),
        "problems": package.get("problems", []),
        "items": [_with_name(state, item) for item in change.get("items", [])],
        "sent": [{**e, "to_name": recipient_name(state, e["to"])} for e in sent],
        "agent": change.get("agent"),
        "history": change.get("history", []),
    }


def _request(state, change):
    """The line that opens the original request: an email, or a call or text the trip manager logged."""
    email = change.get("email") or {}
    note = change.get("note")
    if note:
        who = recipient_name(state, email.get("from", "")).split(" (")[0]
        return {"label": f"Change request · {note['channel']} from {who}", "text": note["text"]}
    return {"label": f"Change request · Email · Subj: {email.get('subject') or '(no subject)'}", "text": email.get("body", "")}


# --- Plain-language pieces -----------------------------------------------------

def describe_changes(state: dict, changes: list) -> list:
    """Before → after lines for a set of proposed changes, as a trip manager reads them.
    Uses the current state for "before"; empty if the changes no longer apply."""
    if not changes:
        return []
    try:
        after = apply_changes(state, changes)
    except (ValueError, KeyError):
        return []
    lines, seen = [], set()
    for change in changes:
        op = change["op"]
        if op in ("move_leg", "move_trip"):
            leg_ids = [change["leg_id"]] if op == "move_leg" else [
                e["leg"] for e in trip_by_id(state, change["trip_id"])["legs"]]
            for leg_id in leg_ids:
                if leg_id in seen:
                    continue
                seen.add(leg_id)
                old_dep, old_arr = leg_times(state, leg_by_id(state, leg_id))
                new_dep, new_arr = leg_times(after, leg_by_id(after, leg_id))
                leg = leg_by_id(state, leg_id)
                lines.append({"what": f"{leg_id} {leg['origin']}→{leg['destination']}",
                              "before": f"{label(old_dep)}, arrives {old_arr:%H:%M}",
                              "after": f"{label(new_dep)}, arrives {new_arr:%H:%M}"})
        elif op == "assign_crew":
            for leg_id in change["leg_ids"]:
                before = crew_by_id(state, leg_by_id(state, leg_id)["crew"])["name"]
                lines.append({"what": f"{leg_id} crew", "before": before,
                              "after": crew_by_id(state, change["crew_id"])["name"]})
        elif op == "set_passengers":
            trip = trip_by_id(state, leg_by_id(state, change["leg_id"])["trip"])
            before = next(e["passengers"] for e in trip["legs"] if e["leg"] == change["leg_id"])
            added = [p for p in change["passengers"] if p not in before]
            removed = [p for p in before if p not in change["passengers"]]
            detail = "; ".join(filter(None, [f"adds {', '.join(added)}" if added else "",
                                             f"removes {', '.join(removed)}" if removed else ""]))
            lines.append({"what": f"{change['leg_id']} passengers", "before": f"{len(before)}",
                          "after": f"{len(change['passengers'])}" + (f" ({detail})" if detail else "")})
        elif op == "update_service":
            service = service_by_id(state, change["service_id"])
            vendor = vendor_by_id(state, service["vendor"])["name"]
            line = next((l for l in lines if l.get("service") == service["id"]), None)
            if line is None:
                line = {"what": f"{service['type'].replace('_', ' ').capitalize()} · {vendor}",
                        "service": service["id"], "kind": "service",
                        "parts": [{"text": service["details"], "change": None}]}
                lines.append(line)
            if "details" in change and change["details"] != service["details"]:
                line["parts"] = word_changes(service["details"], change["details"])
            if "status" in change and change["status"] != service["status"]:
                line["status"] = {"before": STATUS_WORDS.get(service["status"], service["status"]),
                                  "after": STATUS_WORDS.get(change["status"], change["status"])}
    return lines


STATUS_WORDS = {"requested": "awaiting confirmation", "confirmed": "confirmed", "unavailable": "unavailable"}


def word_changes(before: str, after: str) -> list:
    """The new text, with only the changed words marked: [{"text", "change": None | "add" | "del"}]."""
    old_words, new_words = before.split(" "), after.split(" ")
    parts = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, old_words, new_words).get_opcodes():
        if op == "equal":
            parts.append({"text": " ".join(new_words[j1:j2]), "change": None})
            continue
        if op in ("delete", "replace"):
            parts.append({"text": " ".join(old_words[i1:i2]), "change": "del"})
        if op in ("insert", "replace"):
            parts.append({"text": " ".join(new_words[j1:j2]), "change": "add"})
    return parts


CHECK_LABELS = {
    # check name: (display name, verdict when passing, verdict when failing)
    "crew_duty": ("Crew duty", "Legal", "Not legal"),
    "aircraft_schedule": ("Aircraft schedule", "No conflicts", "Conflict"),
    "airport_hours": ("Airport hours", "Open", "Closed"),
    "capacity": ("Seats", "Fits", "Over capacity"),
    "watchlist": ("Watchlist", "Clear", "Match"),
    "range_and_flight_time": ("Range", "In range", "Out of range"),
    "aircraft_availability": ("Aircraft availability", "Available", "Busy"),
    "available_crew": ("Available crew", "Found", "None found"),
    "duty_fixes": ("Fix search", "Fixes found", "No fix"),
}


def named_checks(results: list) -> list:
    """Each check as name, verdict, detail; failures first, so the list scans as a column."""
    named = []
    for result in results:
        name, yes, no = CHECK_LABELS.get(result["check"], (result["check"].replace("_", " ").capitalize(), "Pass", "Fail"))
        ok = result["result"] == "pass"
        named.append({"name": name, "ok": ok, "verdict": yes if ok else no, "detail": result["summary"]})
    return sorted(named, key=lambda c: c["ok"])


def recipient_name(state: dict, address: str) -> str:
    """A person or company name for an email address in the data, else the address itself."""
    address = address.strip().lower()
    for client in state["clients"]:
        for contact in client["contacts"]:
            if contact["email"].lower() == address:
                return f"{contact['name']} ({client['company']})"
    for vendor in state["vendors"]:
        if any(c["email"].lower() == address for c in vendor["contacts"]):
            return f"{vendor['name']} ({vendor['airport']})"
    for crew in state["crew"]:
        for member in crew["members"]:
            if member["email"].lower() == address:
                return f"{member['name']} ({member['role']})"
    for person in state["operator"]["people"]:
        if person["email"].lower() == address:
            return f"{person['name']} ({person['role']})"
    return address


def _with_name(state, item):
    if item["kind"] != "message":
        return item
    return {**item, "to_name": recipient_name(state, item["message"]["to"])}


# --- Reference -------------------------------------------------------------------

STATIC = Path(__file__).parent / "static"


def reference(state: dict) -> dict:
    """The Reference view: aircraft (with photos), pilots, airports (favorites) with their
    vendors, and clients. Read-only, from the data files. Schedules live on the schedule board."""
    photos = json.loads((STATIC / "photos" / "photos.json").read_text())
    maps = json.loads((STATIC / "maps" / "maps.json").read_text())
    details = json.loads((DATA_DIR / "airport_details.json").read_text())
    fleet_types = {a["type"]: a["type_name"] for a in state["fleet"]}

    aircraft = [{**{k: a[k] for k in ("tail", "type_name", "seats", "cruise_kt", "range_nm", "base")},
                 "photos": photos.get(a["type"], [])} for a in state["fleet"]]
    pilots = []
    for crew in state["crew"]:
        for member in crew["members"]:
            partners = [m["name"] for m in crew["members"] if m is not member]
            pilots.append({"id": _slug(member["name"]), "name": member["name"], "role": member["role"],
                           "email": member["email"], "phone": member["phone"], "crew": crew["id"],
                           "rating": fleet_types.get(crew["rating"], crew["rating"]), "base": crew["base"],
                           "flies_with": partners})
    airports = [{"code": a["code"], "name": a["name"], **details.get(a["code"], {}), "favorite": True,
                 "hours": "Open 24 hours" if (a["open"], a["close"]) == ("00:00", "24:00")
                          else f"Open {a['open']}–{a['close']}",
                 "map": maps.get(a["code"]),
                 "vendors": [{"id": v["id"], "name": v["name"], "kind": v["kind"], "contacts": v["contacts"],
                              "services": v["services"]} for v in state["vendors"] if v["airport"] == a["code"]]}
                for a in state["airports"]]
    people = json.loads((DATA_DIR / "client_people.json").read_text())["clients"]
    current = now(state)
    clients = []
    for c in state["clients"]:
        emails = {contact["name"]: contact["email"] for contact in c["contacts"]}
        upcoming = sorted((min(leg_by_id(state, e["leg"])["departure"] for e in t["legs"]), t["id"])
                          for t in state["trips"] if t["client"] == c["id"]
                          and any(parse(leg_by_id(state, e["leg"])["departure"]) >= current for e in t["legs"]))
        clients.append({
            "id": c["id"], "company": c["company"],
            "trip_count": sum(1 for t in state["trips"] if t["client"] == c["id"]),
            "next_trip": upcoming[0][1] if upcoming else None,
            # Contacts (principal, assistant) show by default; everyone else expands.
            "people": [{**person, "id": _slug(person["name"]), "email": emails.get(person["name"]),
                        "contact": person["name"] in emails} for person in people.get(c["id"], [])],
        })
    return {"aircraft": aircraft, "pilots": pilots, "airports": airports, "clients": clients,
            "directory_size": 5000}


def _slug(name):
    return name.lower().replace(" ", "-")


# --- Schedule board -----------------------------------------------------------------

def schedule_board(state: dict) -> dict:
    """The schedule board: the demo week's flights, maintenance, standby, and duty days, by
    aircraft and by pilot, plus any leg moves waiting for the trip manager's approval.
    The page only lays these out on a timeline; all times come from the flight-time and
    duty tools the checks use."""
    from datetime import timedelta
    from agentflight.tools.duty import duty_days
    from agentflight.times import duration, to_text

    current = now(state)
    monday = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0)
    trips = {trip["id"]: trip for trip in state["trips"]}
    short = {c["id"]: c["company"].split()[0] for c in state["clients"]}
    members = {c["id"]: [_slug(m["name"]) for m in c["members"]] for c in state["crew"]}

    def leg_item(leg):
        departs, arrives = leg_times(state, leg)
        trip = trips.get(leg["trip"])
        route = f"{leg['origin']}→{leg['destination']}"
        return {"kind": "client" if trip else "positioning", "start": to_text(departs), "end": to_text(arrives),
                "label": f"{route} · {short[trip['client']]}" if trip else route, "time": f"{departs:%H:%M}",
                "trip": leg["trip"], "client": trip["client"] if trip else None, "leg": leg["id"],
                "aircraft": leg["aircraft"], "pilots": members[leg["crew"]]}

    flights = [leg_item(leg) for leg in state["schedule"]["legs"]]
    # Leg moves in change packages waiting for a decision: drawn under the leg they replace.
    proposed = []
    for change in state["queue"]:
        if change["status"] not in DECISION or not change.get("package"):
            continue
        for moved in (change["package"].get("change_package") or {}).get("legs", []):
            leg = leg_by_id(state, moved["leg"])
            current_item = next(f for f in flights if f["leg"] == leg["id"])
            proposed.append({**current_item, "kind": "proposed", "start": moved["departure"], "end": moved["arrival"],
                             "time": moved["departure"][-5:], "change": change["id"],
                             "label": f"Proposed: {moved['origin']}→{moved['destination']} at {moved['departure'][-5:]}"})
    maintenance = [{"kind": "maintenance", "start": m["start"], "end": m["end"], "label": m["airport"],
                    "time": m["start"][-5:], "aircraft": m["aircraft"], "note": m["note"]}
                   for m in state["schedule"]["maintenance"]]
    standby = [{"kind": "standby", "start": b["start"], "end": b["end"], "label": b["airport"],
                "time": b["start"][-5:], "pilots": members[b["crew"]]} for b in state["schedule"]["standby"]]

    aircraft_rows = [{"id": a["tail"], "title": a["tail"], "subtitle": a["type_name"],
                      "items": _by_start([i for i in flights + proposed + maintenance if i.get("aircraft") == a["tail"]])}
                     for a in state["fleet"]]
    pilot_rows = []
    for crew in state["crew"]:
        duty = [{"start": to_text(d["start"]), "end": to_text(d["end"]), "label": f"Duty {duration(d['minutes'])}"}
                for d in duty_days(state, crew["id"])]
        for member in crew["members"]:
            pid = _slug(member["name"])
            pilot_rows.append({"id": pid, "title": member["name"], "subtitle": f"{member['role']} · {crew['name']}",
                               "duty": duty,
                               "items": _by_start([i for i in flights + proposed + standby if pid in i.get("pilots", [])])})
    return {
        "now": state["clock"]["now"],
        "days": [{"date": to_text(monday + timedelta(days=n))[:10], "label": day_label(monday + timedelta(days=n))}
                 for n in range(7)],
        "today": current.weekday(),
        "aircraft": aircraft_rows,
        "pilots": pilot_rows,
        "clients": [{"id": c["id"], "company": c["company"]} for c in state["clients"]],
    }


def _by_start(items):
    return sorted(items, key=lambda item: (item["start"], item["kind"] == "proposed"))
