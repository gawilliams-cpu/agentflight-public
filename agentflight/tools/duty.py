"""Crew duty and rest (simplified rules, stated as such).

- A duty day is one calendar day of work.
- Duty starts 1 hour before the first departure, or at the start of standby
  if that is earlier. Standby counts as duty, never as rest.
- Duty ends 30 minutes after the last arrival, or at the end of standby if
  that is later.
- Maximum duty day: 14 hours. Minimum rest between duty days: 10 hours.
- The crew must be rated on the aircraft type of every leg they fly.
- The crew must be where each leg departs: at their base, their standby
  airport, or where their last leg landed.
"""

from datetime import timedelta

from agentflight.store import aircraft_by_tail, crew_by_id, leg_by_id
from agentflight.times import day_label, duration, minutes_between, parse
from agentflight.tools import check_result
from agentflight.tools.flight import changed_leg_times, leg_times
from agentflight.whatif import apply_changes, touched_leg_ids

MAX_DUTY_MINUTES = 14 * 60
MIN_REST_MINUTES = 10 * 60
REPORT_BEFORE_DEPARTURE = timedelta(hours=1)
RELEASE_AFTER_ARRIVAL = timedelta(minutes=30)


def duty_days(state: dict, crew_id: str) -> list:
    """Each day the crew works, in date order: start, end, minutes, and activities."""
    days = {}

    def add(day, start, end, activity):
        entry = days.setdefault(day, {"date": day, "start": start, "end": end, "activities": []})
        entry["start"] = min(entry["start"], start)
        entry["end"] = max(entry["end"], end)
        entry["activities"].append(activity)

    for leg in state["schedule"]["legs"]:
        if leg["crew"] == crew_id:
            departure, arrival = leg_times(state, leg)
            add(departure.date(), departure - REPORT_BEFORE_DEPARTURE,
                arrival + RELEASE_AFTER_ARRIVAL, leg["id"])
    for block in state["schedule"]["standby"]:
        if block["crew"] == crew_id:
            start, end = parse(block["start"]), parse(block["end"])
            add(start.date(), start, end, f"standby at {block['airport']}")

    ordered = [days[d] for d in sorted(days)]
    for day in ordered:
        day["minutes"] = minutes_between(day["start"], day["end"])
    return ordered


def rest_periods(days: list) -> list:
    """The rest between each pair of consecutive duty days."""
    return [
        {"after": earlier["date"], "before": later["date"],
         "minutes": minutes_between(earlier["end"], later["start"])}
        for earlier, later in zip(days, days[1:])
    ]


def check_crew_duty(state: dict, changes: list | None = None, crew_ids: list | None = None) -> dict:
    """Check duty, rest, and type rating for crews after the proposed changes.

    With changes, checks every crew on the touched legs, before or after the
    change. With crew_ids, checks those crews. Reports the touched days in the
    summary; a problem on any day of the week fails the check.
    """
    changes = changes or []
    proposed = apply_changes(state, changes)
    touched = touched_leg_ids(state, changes)

    if crew_ids is None:
        crew_ids = []
        for leg_id in touched:
            for s in (state, proposed):
                crew_ids.append(leg_by_id(s, leg_id)["crew"])
        crew_ids = list(dict.fromkeys(crew_ids))
    if not crew_ids:
        raise ValueError("No crew to check: pass crew_ids, or changes that touch legs")

    focus_dates = {leg_times(s, leg_by_id(s, leg_id))[0].date()
                   for leg_id in touched for s in (state, proposed)}

    lines, problem_lines, crews, all_ok = [], [], [], True
    for crew_id in crew_ids:
        crew = crew_by_id(proposed, crew_id)
        days = duty_days(proposed, crew_id)
        rests = rest_periods(days)
        # Problems anywhere in the week fail the check. They come first, so
        # the headline of a failed check is the reason it failed.
        problems = _rating_problems(proposed, crew) + _location_problems(proposed, crew)
        for d in days:
            if d["minutes"] > MAX_DUTY_MINUTES:
                problems.append(_day_line(crew["name"], d))
        for r in rests:
            if r["minutes"] < MIN_REST_MINUTES:
                problems.append(_rest_line(crew["name"], days, r))
        problem_lines.extend(problems)
        all_ok = all_ok and not problems

        shown_days = [d for d in days if not focus_dates or d["date"] in focus_dates]
        shown_rests = [r for r in rests
                       if not focus_dates or r["after"] in focus_dates or r["before"] in focus_dates]
        lines.extend(_day_line(crew["name"], d) for d in shown_days)
        lines.extend(_rest_line(crew["name"], days, r) for r in shown_rests)

        crews.append({
            "crew": crew_id,
            "name": crew["name"],
            "duty_days": [_day_details(d) for d in days],
            "rest_periods": [{"before": r["before"].isoformat(), "minutes": r["minutes"],
                              "rest": duration(r["minutes"]),
                              "legal": r["minutes"] >= MIN_REST_MINUTES} for r in rests],
            "problems": problems,
        })

    legs = changed_leg_times(state, changes)
    lines = (problem_lines + [line for line in lines if line not in problem_lines]
             + [leg["text"] for leg in legs])
    return check_result("crew_duty", all_ok, lines, legs=legs, crews=crews,
                        max_duty_minutes=MAX_DUTY_MINUTES, min_rest_minutes=MIN_REST_MINUTES)


def _rating_problems(state, crew):
    problems = []
    for leg in state["schedule"]["legs"]:
        if leg["crew"] == crew["id"]:
            aircraft = aircraft_by_tail(state, leg["aircraft"])
            if aircraft["type"] != crew["rating"]:
                problems.append(f"{crew['name']} is not rated on the {aircraft['type_name']} "
                                f"assigned to leg {leg['id']}.")
    return problems


def _location_problems(state, crew):
    """Walk the crew's week in time order and check each leg departs where they are."""
    items = []
    for leg in state["schedule"]["legs"]:
        if leg["crew"] == crew["id"]:
            departure, _ = leg_times(state, leg)
            items.append((departure, leg))
    for block in state["schedule"]["standby"]:
        if block["crew"] == crew["id"]:
            items.append((parse(block["start"]), {"standby_at": block["airport"]}))

    ordered = [item for _, item in sorted(items, key=lambda pair: pair[0])]
    problems, location = [], crew["base"]
    for i, item in enumerate(ordered):
        if "standby_at" in item:
            location = item["standby_at"]
            continue
        if item["origin"] != location:
            remaining = [later["id"] for later in ordered[i:] if "id" in later]
            problems.append(f"{crew['name']} would be at {location}, but leg {item['id']} "
                            f"departs {item['origin']}. Their legs from there on "
                            f"({', '.join(remaining)}) need a crew that is at {item['origin']}.")
        location = item["destination"]
    return problems


def _day(days, date):
    return next(day_label(d["start"]) for d in days if d["date"] == date)


def _day_line(name, d):
    spare = MAX_DUTY_MINUTES - d["minutes"]
    verdict = (f"legal, {duration(spare)} under the limit" if spare >= 0
               else f"over by {duration(-spare)}")
    return (f"{name} {day_label(d['start'])}: on duty {d['start']:%H:%M}–{d['end']:%H:%M}, "
            f"{duration(d['minutes'])} against the 14 h limit — {verdict}.")


def _rest_line(name, days, r):
    short = MIN_REST_MINUTES - r["minutes"]
    verdict = "legal" if short <= 0 else f"under the 10 h minimum by {duration(short)}"
    return (f"{name} rest before {_day(days, r['before'])}: {duration(r['minutes'])} "
            f"against the 10 h minimum — {verdict}.")


def _day_details(d):
    return {
        "date": d["date"].isoformat(),
        "start": f"{d['start']:%H:%M}",
        "end": f"{d['end']:%H:%M}",
        "minutes": d["minutes"],
        "duty": duration(d["minutes"]),
        "legal": d["minutes"] <= MAX_DUTY_MINUTES,
        "activities": d["activities"],
    }
