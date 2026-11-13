"""Proposed changes, applied to a copy of the state.

A change is a small dict with an "op". The checks apply changes to a copy and
test the result; the real state is never touched here. The same changes will
later drive the agent's options, trip chat what-ifs, and the re-check on
approval.

Supported changes:
  {"op": "move_leg", "leg_id": "A1-2", "departure": "2026-10-29T17:00"}
  {"op": "assign_crew", "leg_ids": ["A1-2"], "crew_id": "nandakumar-kessler"}
  {"op": "move_trip", "trip_id": "A1", "date": "2026-10-28"}   (keeps each leg's time of day)
  {"op": "set_passengers", "leg_id": "A1-2", "passengers": ["...", "..."]}
  {"op": "update_service", "service_id": "A1-S3", "details": "...", "status": "confirmed"}
     (details and status are each optional; status is requested, confirmed, or unavailable)
"""

from datetime import date

from agentflight.store import copy_state, crew_by_id, leg_by_id, service_by_id, trip_by_id
from agentflight.times import parse, to_text


REQUIRED_FIELDS = {
    "move_leg": ("leg_id", "departure"),
    "assign_crew": ("leg_ids", "crew_id"),
    "move_trip": ("trip_id", "date"),
    "set_passengers": ("leg_id", "passengers"),
    "update_service": ("service_id",),
}
SERVICE_STATUSES = ("requested", "confirmed", "unavailable")


def apply_changes(state: dict, changes: list) -> dict:
    proposed = copy_state(state)
    for change in changes:
        op = change.get("op")
        _require(change, REQUIRED_FIELDS.get(op, ()))
        if op == "move_leg":
            parse(change["departure"])  # reject a malformed time before using it
            leg_by_id(proposed, change["leg_id"])["departure"] = change["departure"]
        elif op == "assign_crew":
            crew_by_id(proposed, change["crew_id"])
            for leg_id in change["leg_ids"]:
                leg_by_id(proposed, leg_id)["crew"] = change["crew_id"]
        elif op == "move_trip":
            _move_trip(proposed, change["trip_id"], date.fromisoformat(change["date"]))
        elif op == "set_passengers":
            _trip_leg_entry(proposed, change["leg_id"])["passengers"] = list(change["passengers"])
        elif op == "update_service":
            service = service_by_id(proposed, change["service_id"])
            if "status" in change:
                if change["status"] not in SERVICE_STATUSES:
                    raise ValueError(f"Service status must be one of {SERVICE_STATUSES}")
                service["status"] = change["status"]
            if "details" in change:
                service["details"] = change["details"]
        else:
            raise ValueError(f"Unknown change op: {op!r}")
    return proposed


def touched_leg_ids(state: dict, changes: list) -> list:
    """The legs a set of changes touches, in order, without repeats."""
    ids = []
    for change in changes:
        op = change.get("op")
        if op in ("move_leg", "set_passengers"):
            ids.append(change["leg_id"])
        elif op == "assign_crew":
            ids.extend(change["leg_ids"])
        elif op == "move_trip":
            ids.extend(entry["leg"] for entry in trip_by_id(state, change["trip_id"])["legs"])
    return list(dict.fromkeys(ids))


def _require(change: dict, fields: tuple) -> None:
    missing = [name for name in fields if name not in change]
    if missing:
        raise ValueError(f"Change {change.get('op')!r} is missing {', '.join(missing)}")


def _move_trip(state: dict, trip_id: str, new_date: date) -> None:
    legs = [leg_by_id(state, entry["leg"]) for entry in trip_by_id(state, trip_id)["legs"]]
    first_date = min(parse(leg["departure"]).date() for leg in legs)
    shift = new_date - first_date
    for leg in legs:
        leg["departure"] = to_text(parse(leg["departure"]) + shift)


def _trip_leg_entry(state: dict, leg_id: str) -> dict:
    leg = leg_by_id(state, leg_id)
    if leg["trip"] is None:
        raise ValueError(f"Leg {leg_id} is a positioning leg and has no passengers")
    for entry in trip_by_id(state, leg["trip"])["legs"]:
        if entry["leg"] == leg_id:
            return entry
    raise ValueError(f"Leg {leg_id} is not listed on trip {leg['trip']}")
