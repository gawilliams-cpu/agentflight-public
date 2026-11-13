"""Which checks apply to a set of changes, and the search for fixes when crew duty fails.

checks_for_changes() is the one place that decides which checks a change needs.
The package verification and find_duty_fixes() both use it, so an option the
search offers is checked exactly the way the package will re-check it.
"""

from datetime import timedelta

from agentflight.store import aircraft_by_tail, crew_by_id, leg_by_id, trip_by_id
from agentflight.times import parse, to_text
from agentflight.tools.checks import (check_aircraft_schedule, check_airport_hours, check_capacity,
                                      check_watchlist, find_available_crew)
from agentflight.tools.duty import check_crew_duty
from agentflight.tools.fees import compute_change_fee
from agentflight.tools.flight import changed_leg_times, leg_times
from agentflight.whatif import apply_changes, touched_leg_ids

STEP = timedelta(minutes=15)       # fixes use quarter-hour times
MAX_SHIFT = timedelta(hours=3)     # furthest an earlier leg is moved later
SCHEDULE_OPS = ("move_leg", "assign_crew", "move_trip")


def checks_for_changes(state: dict, changes: list) -> list:
    """Run every check that applies to these changes. Raises ValueError if they can't be applied."""
    if not changes:
        return []
    proposed = apply_changes(state, changes)
    results = []

    timing = [c for c in changes if c["op"] in SCHEDULE_OPS]
    if timing:
        results.append(check_crew_duty(state, timing))
        aircraft = {leg_by_id(proposed, leg_id)["aircraft"] for leg_id in touched_leg_ids(state, timing)}
        results += [check_aircraft_schedule(state, tail, timing) for tail in sorted(aircraft)]
        moved = [c for c in changes if c["op"] in ("move_leg", "move_trip")]
        for leg_id in touched_leg_ids(state, moved):
            leg = leg_by_id(proposed, leg_id)
            departure, arrival = leg_times(proposed, leg)
            results.append(check_airport_hours(proposed, leg["origin"], to_text(departure)))
            results.append(check_airport_hours(proposed, leg["destination"], to_text(arrival)))

    for change in changes:
        if change["op"] == "set_passengers":
            leg = leg_by_id(proposed, change["leg_id"])
            results.append(check_capacity(proposed, leg["aircraft"], len(change["passengers"])))
            trip = trip_by_id(state, leg_by_id(state, change["leg_id"])["trip"])
            before = next(e["passengers"] for e in trip["legs"] if e["leg"] == change["leg_id"])
            added = [p for p in change["passengers"] if p not in before]
            if added:
                results.append(check_watchlist(state, added))
    return results


def find_duty_fixes(state: dict, changes: list) -> dict:
    """When a requested time change breaks crew duty, find the standard fixes that pass.

    Tries three kinds of fix, each checked in full and priced from the rate card:
      1. the latest departure the current crew can legally fly,
      2. the requested time, with the crew's first leg that day moved later,
      3. the requested time, with an available crew taking the leg (plus any of
         the aircraft's later legs needed to keep everyone in position).
    """
    moves = [c for c in changes if c["op"] == "move_leg"]
    if not moves:
        raise ValueError("find_duty_fixes needs the requested change as a move_leg")
    target_id = moves[0]["leg_id"]
    proposed = apply_changes(state, changes)
    target = leg_by_id(proposed, target_id)
    current_departure = parse(leg_by_id(state, target_id)["departure"])
    requested = parse(target["departure"])
    crew = crew_by_id(proposed, target["crew"])

    if _all_pass(checks_for_changes(state, changes)):
        return _result([], ["The requested change already passes every check; no fix needed."])

    fixes = []
    for finder in (_latest_legal_time, _move_first_leg_later, _swap_crew):
        fix = finder(state, changes, target, crew, current_departure, requested)
        if fix:
            fixes.append(_priced(state, fix))

    lines = [f"Requested: {target_id} at {requested:%H:%M} fails crew duty for {crew['name']}."]
    if fixes:
        lines += [f"Fix {i}: {f['description']} Passes every check. Charges ${f['fee_total']:,}."
                  for i, f in enumerate(fixes, start=1)]
    else:
        lines.append("No standard fix passes. Hold the change and say so plainly.")
    return _result(fixes, lines)


def _latest_legal_time(state, changes, target, crew, current_departure, requested):
    """Step back from the requested time toward the current one."""
    moment = requested - STEP
    while moment > current_departure:
        candidate = _replace_departure(changes, target["id"], moment)
        if _all_pass(checks_for_changes(state, candidate)):
            return {"kind": "latest_legal_time", "changes": candidate,
                    "description": f"Depart {target['id']} at {moment:%H:%M} instead of "
                                   f"{requested:%H:%M}, with the same crew ({crew['name']})."}
        moment -= STEP
    return None


def _move_first_leg_later(state, changes, target, crew, current_departure, requested):
    """Keep the requested time; start the crew's day later by moving their first leg."""
    proposed = apply_changes(state, changes)
    same_day = sorted((leg for leg in proposed["schedule"]["legs"]
                       if leg["crew"] == crew["id"] and parse(leg["departure"]).date() == requested.date()),
                      key=lambda leg: leg["departure"])
    if not same_day or same_day[0]["id"] == target["id"]:
        return None
    first_leg_id = same_day[0]["id"]
    first_departure = parse(same_day[0]["departure"])
    shift = STEP
    while shift <= MAX_SHIFT:
        new_time = first_departure + shift
        candidate = changes + [{"op": "move_leg", "leg_id": first_leg_id, "departure": to_text(new_time)}]
        if _all_pass(checks_for_changes(state, candidate)):
            return {"kind": "move_earlier_leg", "changes": candidate,
                    "description": f"Depart {target['id']} at {requested:%H:%M} as requested, and move "
                                   f"{first_leg_id} from {first_departure:%H:%M} to {new_time:%H:%M} "
                                   f"so {crew['name']} start later."}
        shift += STEP
    return None


def _swap_crew(state, changes, target, crew, current_departure, requested):
    """Keep the requested time; an available crew flies the leg instead."""
    proposed = apply_changes(state, changes)
    departure, arrival = leg_times(proposed, target)
    aircraft = aircraft_by_tail(proposed, target["aircraft"])
    available = find_available_crew(state, target["origin"], aircraft["type"],
                                    to_text(departure), to_text(arrival), changes)
    # The aircraft's later legs flown by the current crew, in time order.
    later = sorted((leg for leg in proposed["schedule"]["legs"]
                    if leg["aircraft"] == target["aircraft"] and leg["crew"] == crew["id"]
                    and parse(leg["departure"]) > departure),
                   key=lambda leg: leg["departure"])

    for candidate_crew in available["details"]["candidates"]:
        for count in range(len(later) + 1):
            legs = [target["id"]] + [leg["id"] for leg in later[:count]]
            candidate = changes + [{"op": "assign_crew", "leg_ids": legs,
                                    "crew_id": candidate_crew["crew"]}]
            if _all_pass(checks_for_changes(state, candidate)):
                kept = [leg["id"] for leg in later[count:]]
                description = (f"Depart {target['id']} at {requested:%H:%M} as requested, flown by "
                               f"{candidate_crew['name']} instead of {crew['name']}: they fly "
                               f"{', '.join(legs)}.")
                if kept:
                    description += f" {crew['name']} keep {', '.join(kept)}."
                return {"kind": "crew_swap", "changes": candidate, "swaps": 1,
                        "description": description}
    return None


def _priced(state, fix):
    """Price a fix: a time change fee for each moved leg, plus the crew swap charge if any."""
    moved = [c["leg_id"] for c in fix["changes"] if c["op"] == "move_leg"
             and c["departure"] != leg_by_id(state, c["leg_id"])["departure"]]
    fees = []
    if moved:
        fees.append({"charge": "departure_time_change", "leg_ids": moved})
    if fix.get("swaps"):
        fees.append({"charge": "crew_swap", "count": fix["swaps"]})
    priced = [compute_change_fee(state, f["charge"], leg_ids=f.get("leg_ids"), count=f.get("count", 1))
              for f in fees]
    legs = changed_leg_times(state, fix["changes"])
    return {
        "kind": fix["kind"],
        "description": fix["description"] + " " + " ".join(leg["text"] for leg in legs),
        "legs": legs,
        "changes": fix["changes"],
        "fees": fees,
        "fee_total": sum(p["amount"] for p in priced),
        "fee_explanation": [p["summary"] for p in priced],
    }


def _replace_departure(changes, leg_id, moment):
    return [({**c, "departure": to_text(moment)} if c["op"] == "move_leg" and c["leg_id"] == leg_id else c)
            for c in changes]


def _all_pass(results):
    return all(r["result"] == "pass" for r in results)


def _result(fixes, lines):
    return {"check": "duty_fixes", "result": "pass" if fixes else "fail",
            "summary": "\n".join(lines), "details": {"fixes": fixes}}
