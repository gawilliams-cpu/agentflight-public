"""Read tools: look things up in the data. These never change anything."""

from datetime import date

from agentflight.store import (aircraft_by_tail, client_by_id, crew_by_id, leg_by_id, now,
                               trip_by_id, vendor_by_id)
from agentflight.times import parse, to_text
from agentflight.tools.flight import leg_times


def find_trips_for_sender(state: dict, sender_email: str) -> dict:
    """Who sent this, and which upcoming trips are they part of?"""
    email = sender_email.strip().lower()
    identity = {"kind": "unknown", "email": email}
    trip_ids = []

    for client in state["clients"]:
        for contact in client["contacts"]:
            if contact["email"].lower() == email:
                identity = {"kind": "client", "email": email, "name": contact["name"],
                            "role": contact["role"], "client": client["id"],
                            "company": client["company"]}
                trip_ids = [t["id"] for t in state["trips"] if t["client"] == client["id"]]
    for vendor in state["vendors"]:
        if any(c["email"].lower() == email for c in vendor["contacts"]):
            identity = {"kind": "vendor", "email": email, "vendor": vendor["id"],
                        "name": vendor["name"], "airport": vendor["airport"]}
            trip_ids = [t["id"] for t in state["trips"]
                        if any(s["vendor"] == vendor["id"] for s in t["services"])]
    for crew in state["crew"]:
        for member in crew["members"]:
            if member["email"].lower() == email:
                identity = {"kind": "crew", "email": email, "name": member["name"],
                            "role": member["role"], "crew": crew["id"]}
                trip_ids = list(dict.fromkeys(
                    leg["trip"] for leg in state["schedule"]["legs"]
                    if leg["crew"] == crew["id"] and leg["trip"]))
    for person in state["operator"]["people"]:
        if person["email"].lower() == email:
            identity = {"kind": "operator", "email": email, "name": person["name"],
                        "role": person["role"]}

    upcoming = [_trip_summary(state, trip_by_id(state, t)) for t in trip_ids]
    upcoming = [t for t in upcoming if parse(t["last_departure"]) >= now(state)]
    return {"sender": identity, "upcoming_trips": upcoming}


def get_trip(state: dict, trip_id: str) -> dict:
    """The full trip record, with legs, crew, passengers, and services spelled out."""
    trip = trip_by_id(state, trip_id)
    client = client_by_id(state, trip["client"])
    legs = []
    for entry in trip["legs"]:
        leg = leg_by_id(state, entry["leg"])
        departure, arrival = leg_times(state, leg)
        aircraft = aircraft_by_tail(state, leg["aircraft"])
        crew = crew_by_id(state, leg["crew"])
        legs.append({
            "leg": leg["id"],
            "origin": leg["origin"],
            "destination": leg["destination"],
            "departure": to_text(departure),
            "arrival": to_text(arrival),
            "aircraft": {"tail": aircraft["tail"], "type": aircraft["type_name"],
                         "seats": aircraft["seats"]},
            "crew": {"id": crew["id"], "name": crew["name"], "members": crew["members"]},
            "passengers": entry["passengers"],
            "passenger_count": len(entry["passengers"]),
        })
    services = []
    for service in trip["services"]:
        vendor = vendor_by_id(state, service["vendor"])
        how = next((s["how"] for s in vendor["services"] if s["type"] == service["type"]), None)
        services.append({**service, "vendor_name": vendor["name"], "how": how,
                         "vendor_contacts": vendor["contacts"]})
    return {
        "trip": trip["id"],
        "title": trip["title"],
        "client": client,
        "legs": legs,
        "services": services,
        "notes": trip["notes"],
        "options_awaiting_client": options_awaiting_client(state, trip["id"]),
    }


def options_awaiting_client(state: dict, trip_id: str) -> list:
    """Options we emailed the client for this trip that they haven't answered yet, from our own
    records (never from the client's quoted reply). Each was verified and priced when offered."""
    open_requests = []
    for change in state.get("queue", []):
        if change.get("trip") != trip_id or (change.get("status"), change.get("reason")) != ("Waiting", "On client"):
            continue
        clarification = (change.get("package") or {}).get("clarification") or {}
        sent = [e for e in state.get("outbox", []) if e.get("change") == change["id"]]
        open_requests.append({
            "request": change["id"],
            "emailed_to": [e["to"] for e in sent],
            "emailed_at": sent[0]["sent_at"] if sent else None,
            "options": [{k: option.get(k) for k in ("label", "description", "changes", "fees", "legs")}
                        for option in clarification.get("options", [])],
        })
    return open_requests


def get_schedule(state: dict, start_date: str, end_date: str) -> dict:
    """Every leg, maintenance block, and standby block between two dates (inclusive)."""
    first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)

    def in_range(moment):
        return first <= moment.date() <= last

    legs = []
    for leg in state["schedule"]["legs"]:
        departure, arrival = leg_times(state, leg)
        if in_range(departure):
            legs.append({**leg, "arrival": to_text(arrival),
                         "client": trip_by_id(state, leg["trip"])["client"] if leg["trip"] else None})
    return {
        "legs": sorted(legs, key=lambda leg: leg["departure"]),
        "maintenance": [b for b in state["schedule"]["maintenance"] if in_range(parse(b["start"]))],
        "standby": [b for b in state["schedule"]["standby"] if in_range(parse(b["start"]))],
    }


def get_vendors(state: dict, airport: str, service: str | None = None) -> dict:
    """Vendors at an airport, optionally only those offering one service type."""
    vendors = [v for v in state["vendors"] if v["airport"] == airport]
    if service:
        vendors = [v for v in vendors if any(s["type"] == service for s in v["services"])]
    return {"airport": airport, "service": service, "vendors": vendors}


def get_fleet(state: dict) -> dict:
    """Every aircraft the operator has: tail, type, seats, cruise speed, range, and base.
    A lookup only; whether an aircraft is free or where it is comes from the checks."""
    return {"aircraft": [{k: a[k] for k in ("tail", "type", "type_name", "seats", "cruise_kt", "range_nm", "base")}
                         for a in state["fleet"]]}


def get_rate_card(state: dict) -> dict:
    return state["rate_card"]


def _trip_summary(state, trip):
    legs = [leg_by_id(state, e["leg"]) for e in trip["legs"]]
    departures = sorted(leg["departure"] for leg in legs)
    route = "–".join([legs[0]["origin"]] + [leg["destination"] for leg in legs])
    return {"trip": trip["id"], "title": trip["title"], "client": trip["client"],
            "route": route, "first_departure": departures[0], "last_departure": departures[-1]}
