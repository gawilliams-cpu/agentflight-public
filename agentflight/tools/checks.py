"""Capacity, airport hours, aircraft and crew availability, and the watchlist."""

from agentflight.store import aircraft_by_tail, airport_by_code, client_by_id, trip_by_id
from agentflight.times import label, parse
from agentflight.tools import check_result
from agentflight.tools.duty import RELEASE_AFTER_ARRIVAL, REPORT_BEFORE_DEPARTURE
from agentflight.tools.flight import describe_leg, leg_times
from agentflight.whatif import apply_changes


def check_capacity(state: dict, tail: str, passenger_count: int) -> dict:
    aircraft = aircraft_by_tail(state, tail)
    seats = aircraft["seats"]
    fits = passenger_count <= seats
    if fits:
        line = f"{passenger_count} passengers fit the {aircraft['type_name']}'s {seats} seats."
    else:
        line = (f"{passenger_count} passengers exceed the {aircraft['type_name']}'s {seats} seats "
                f"by {passenger_count - seats}.")
    return check_result("capacity", fits, [line], aircraft=tail, seats=seats,
                        passenger_count=passenger_count)


def check_airport_hours(state: dict, airport: str, at: str) -> dict:
    """Is the airport open at this time? `at` is "YYYY-MM-DDTHH:MM"."""
    info = airport_by_code(state, airport)
    clock = f"{parse(at):%H:%M}"
    open_ = info["open"] <= clock < info["close"]  # "HH:MM" strings compare correctly
    hours = "open 24 hours" if (info["open"], info["close"]) == ("00:00", "24:00") \
        else f"open {info['open']}–{info['close']}"
    verdict = "open" if open_ else "closed"
    line = f"{airport} is {verdict} at {clock} ({hours})."
    return check_result("airport_hours", open_, [line], airport=airport, time=at,
                        open=info["open"], close=info["close"])


def check_aircraft_availability(state: dict, tail: str, start: str, end: str,
                                changes: list | None = None) -> dict:
    """Is the aircraft free between start and end, and where is it at the start?"""
    proposed = apply_changes(state, changes or [])
    aircraft = aircraft_by_tail(proposed, tail)
    window_start, window_end = parse(start), parse(end)

    conflicts = []
    for item in _aircraft_timeline(proposed, tail):
        if item["start"] < window_end and window_start < item["end"]:
            conflicts.append(item["describe"])
    location = _aircraft_location(proposed, tail, window_start)

    window = f"{label(window_start)}–{window_end:%H:%M}"
    if conflicts:
        lines = [f"{tail} ({aircraft['type_name']}) is busy {window}:"] + [f"- {c}" for c in conflicts]
    else:
        lines = [f"{tail} ({aircraft['type_name']}) is free {window}, at {location} at the start."]
    return check_result("aircraft_availability", not conflicts, lines, aircraft=tail,
                        start=start, end=end, location_at_start=location, conflicts=conflicts)


def check_aircraft_schedule(state: dict, tail: str, changes: list | None = None) -> dict:
    """After the changes, does the aircraft's week still hold together?

    Fails if two items overlap, or if a leg departs from somewhere other than
    where the aircraft last landed.
    """
    proposed = apply_changes(state, changes or [])
    aircraft = aircraft_by_tail(proposed, tail)
    timeline = _aircraft_timeline(proposed, tail)

    problems = []
    location = aircraft["base"]
    for earlier, later in zip(timeline, timeline[1:]):
        if later["start"] < earlier["end"]:
            problems.append(f"{later['describe']} overlaps {earlier['describe']}.")
    for item in timeline:
        if item["origin"] != location:
            problems.append(f"{item['describe']} departs {item['origin']}, "
                            f"but the aircraft will be at {location}.")
        location = item["destination"]

    if problems:
        lines = [f"{tail} ({aircraft['type_name']}) schedule conflicts:"] + [f"- {p}" for p in problems]
    else:
        lines = [f"{tail} ({aircraft['type_name']}) schedule holds together: no overlaps, "
                 f"and every leg departs where the aircraft is."]
    return check_result("aircraft_schedule", not problems, lines, aircraft=tail, problems=problems)


def find_available_crew(state: dict, airport: str, aircraft_type: str, start: str, end: str,
                        changes: list | None = None) -> dict:
    """Crews rated on the type, at the airport at the start, with no flying in the window.

    Standby crews count as available: that is what standby is for. Run
    check_crew_duty on any assignment before offering it.
    """
    proposed = apply_changes(state, changes or [])
    window_start, window_end = parse(start), parse(end)

    candidates = []
    for crew in proposed["crew"]:
        if crew["rating"] != aircraft_type:
            continue
        busy = False
        for leg in proposed["schedule"]["legs"]:
            if leg["crew"] != crew["id"]:
                continue
            departure, arrival = leg_times(proposed, leg)
            if (departure - REPORT_BEFORE_DEPARTURE < window_end
                    and window_start < arrival + RELEASE_AFTER_ARRIVAL):
                busy = True
        location = _crew_location(proposed, crew, window_start)
        if busy or location != airport:
            continue
        standby = [f"standby at {b['airport']} {parse(b['start']):%H:%M}–{parse(b['end']):%H:%M}"
                   for b in proposed["schedule"]["standby"]
                   if b["crew"] == crew["id"] and parse(b["start"]).date() == window_start.date()]
        candidates.append({"crew": crew["id"], "name": crew["name"], "location": location,
                           "standby": standby})

    window = f"{label(window_start)}–{window_end:%H:%M}"
    if candidates:
        lines = [f"Crews rated on {aircraft_type} and free at {airport} {window}:"]
        lines += [f"- {c['name']}" + (f" ({'; '.join(c['standby'])})" if c["standby"] else "")
                  for c in candidates]
    else:
        lines = [f"No crew rated on {aircraft_type} is free at {airport} {window}."]
    return check_result("available_crew", bool(candidates), lines, airport=airport,
                        aircraft_type=aircraft_type, start=start, end=end, candidates=candidates)


def check_watchlist(state: dict, names: list) -> dict:
    listed = {_normalize(n) for n in state["watchlist"]["names"]}
    matches = [n for n in names if _normalize(n) in listed]
    if matches:
        lines = [f"Watchlist match: {', '.join(matches)}. Hold for human review."]
    else:
        lines = [f"No watchlist matches among {len(names)} name(s)."]
    return check_result("watchlist", not matches, lines, checked=list(names), matches=matches)


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


def _aircraft_timeline(state: dict, tail: str) -> list:
    """The aircraft's legs and maintenance, in time order."""
    items = []
    for leg in state["schedule"]["legs"]:
        if leg["aircraft"] == tail:
            departure, arrival = leg_times(state, leg)
            if leg["trip"]:
                client = client_by_id(state, trip_by_id(state, leg["trip"])["client"])
                who = f" (Client {client['id']}, {client['company']})"
            else:
                who = " (positioning)"
            items.append({"start": departure, "end": arrival, "origin": leg["origin"],
                          "destination": leg["destination"],
                          "describe": f"leg {describe_leg(state, leg)}{who}"})
    for block in state["schedule"]["maintenance"]:
        if block["aircraft"] == tail:
            start, end = parse(block["start"]), parse(block["end"])
            items.append({"start": start, "end": end, "origin": block["airport"],
                          "destination": block["airport"],
                          "describe": f"maintenance at {block['airport']} {label(start)}–{end:%H:%M}"})
    return sorted(items, key=lambda item: item["start"])


def _aircraft_location(state: dict, tail: str, moment) -> str:
    location = aircraft_by_tail(state, tail)["base"]
    for item in _aircraft_timeline(state, tail):
        if item["end"] <= moment:
            location = item["destination"]
    return location


def _crew_location(state: dict, crew: dict, moment) -> str:
    """Where the crew is: the last airport they landed at, else their base."""
    location, latest = crew["base"], None
    for leg in state["schedule"]["legs"]:
        if leg["crew"] == crew["id"]:
            _, arrival = leg_times(state, leg)
            if arrival <= moment and (latest is None or arrival > latest):
                location, latest = leg["destination"], arrival
    return location
