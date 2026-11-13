"""Distance, flight time, and range.

Flight time = great-circle distance / cruise speed + 0.3 h per leg for taxi,
climb, and descent. No winds. Block time is rounded to the nearest whole
minute, and everything downstream is whole-minute arithmetic.

leg_times() is the only place an arrival time is calculated. Arrivals are never
stored and never worked out by the agent: every tool that shows a leg's times,
current or proposed, gets them from here (proposed ones via changed_leg_times()).
"""

from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt

from agentflight.store import aircraft_by_tail, airport_by_code, leg_by_id
from agentflight.times import duration, label, parse, to_text
from agentflight.tools import check_result
from agentflight.whatif import apply_changes, touched_leg_ids

EARTH_RADIUS_NM = 3440.065
TAXI_CLIMB_DESCENT_HOURS = 0.3


def distance_nm(state: dict, origin: str, destination: str) -> float:
    a = airport_by_code(state, origin)
    b = airport_by_code(state, destination)
    lat1, lon1, lat2, lon2 = map(radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_NM * asin(sqrt(h))


def block_minutes(state: dict, tail: str, origin: str, destination: str) -> int:
    cruise = aircraft_by_tail(state, tail)["cruise_kt"]
    hours = distance_nm(state, origin, destination) / cruise + TAXI_CLIMB_DESCENT_HOURS
    return round(hours * 60)


def leg_times(state: dict, leg: dict) -> tuple[datetime, datetime]:
    """Departure and computed arrival for a leg."""
    departure = parse(leg["departure"])
    minutes = block_minutes(state, leg["aircraft"], leg["origin"], leg["destination"])
    return departure, departure + timedelta(minutes=minutes)


def changed_leg_times(state: dict, changes: list) -> list:
    """Departure and arrival of every leg the changes touch, after the changes."""
    proposed = apply_changes(state, changes)
    legs = []
    for leg_id in touched_leg_ids(state, changes):
        leg = leg_by_id(proposed, leg_id)
        departure, arrival = leg_times(proposed, leg)
        legs.append({
            "leg": leg_id,
            "origin": leg["origin"],
            "destination": leg["destination"],
            "departure": to_text(departure),
            "arrival": to_text(arrival),
            "text": f"{leg_id} {leg['origin']}→{leg['destination']} departs {label(departure)}, "
                    f"arrives {arrival:%H:%M}.",
        })
    return legs


def describe_leg(state: dict, leg: dict) -> str:
    """e.g. "A1-2 BED→PBI Thu 10/29 16:00–18:43"."""
    departure, arrival = leg_times(state, leg)
    return f"{leg['id']} {leg['origin']}→{leg['destination']} {label(departure)}–{arrival:%H:%M}"


def check_range_and_flight_time(state: dict, tail: str, origin: str, destination: str) -> dict:
    aircraft = aircraft_by_tail(state, tail)
    distance = round(distance_nm(state, origin, destination))
    minutes = block_minutes(state, tail, origin, destination)
    in_range = distance <= aircraft["range_nm"]
    route = f"{origin}–{destination}"
    if in_range:
        line = (f"{route} is {distance:,} nm, within the {aircraft['type_name']}'s "
                f"{aircraft['range_nm']:,} nm range. Flight time {duration(minutes)}.")
    else:
        line = (f"{route} is {distance:,} nm, beyond the {aircraft['type_name']}'s "
                f"{aircraft['range_nm']:,} nm range by {distance - aircraft['range_nm']:,} nm.")
    return check_result(
        "range_and_flight_time", in_range, [line],
        aircraft=tail, origin=origin, destination=destination,
        distance_nm=distance, range_nm=aircraft["range_nm"],
        flight_minutes=minutes, flight_time=duration(minutes),
    )
