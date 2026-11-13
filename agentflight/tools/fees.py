"""Change fees and fixed charges, priced only from the rate card.

Every result explains how the amount was worked out, e.g.
"55 h notice → 24–72 h tier → $500 × 1 leg = $500". A charge that isn't in
the rate card comes back as not defined, never as $0, so it gets escalated.
"""

from agentflight.store import leg_by_id, now, trip_by_id
from agentflight.times import minutes_between, parse


def compute_change_fee(state: dict, charge: str, leg_ids: list | None = None,
                       trip_id: str | None = None, count: int = 1) -> dict:
    """Price one rate-card charge.

    - per_affected_leg charges need leg_ids (notice is measured per leg).
    - per_trip charges need trip_id (notice is to the trip's first departure).
    - flat, per_swap, and per_night charges use count.
    """
    card = state["rate_card"]
    entry = card["change_fees"].get(charge) or card["fixed_charges"].get(charge)
    if entry is None:
        known = sorted(card["change_fees"]) + sorted(card["fixed_charges"])
        return {
            "charge": charge,
            "defined": False,
            "amount": None,
            "breakdown": [],
            "summary": (f"No '{charge}' charge is defined in the rate card. Escalate the fee "
                        f"decision to the trip manager; do not invent one. "
                        f"Defined charges: {', '.join(known)}."),
        }

    basis = entry["basis"]
    breakdown = []
    if basis == "per_affected_leg":
        if not leg_ids:
            raise ValueError(f"'{charge}' is charged per affected leg: pass leg_ids")
        for leg_id in leg_ids:
            departure = parse(leg_by_id(state, leg_id)["departure"])
            breakdown.append(_tiered_line(state, entry, departure, f"leg {leg_id}"))
    elif basis == "per_trip":
        if not trip_id:
            raise ValueError(f"'{charge}' is charged per trip: pass trip_id")
        first = min(parse(leg_by_id(state, e["leg"])["departure"])
                    for e in trip_by_id(state, trip_id)["legs"])
        breakdown.append(_tiered_line(state, entry, first, f"trip {trip_id}"))
    else:
        unit = {"per_swap": "swap", "per_night": "night"}.get(basis, "item")
        breakdown.append({"item": f"{count} {unit}{'s' if count != 1 else ''}",
                          "amount": entry["amount"] * count,
                          "explanation": f"${entry['amount']:,} × {count} {unit}"
                                         f"{'s' if count != 1 else ''}"})

    total = sum(line["amount"] for line in breakdown)
    lines = [f"{entry['label']}: ${total:,}."] + [f"- {b['item']}: {b['explanation']}" for b in breakdown]
    return {
        "charge": charge,
        "label": entry["label"],
        "defined": True,
        "amount": total,
        "currency": card["currency"],
        "breakdown": breakdown,
        "summary": "\n".join(lines),
    }


def _tiered_line(state, entry, departure, item):
    notice_hours = minutes_between(now(state), departure) / 60
    if notice_hours < 0:
        raise ValueError(f"{item} departed before now; no change fee applies to a past departure")
    tiers = sorted(entry["tiers"], key=lambda t: t["min_notice_hours"], reverse=True)
    tier = next(t for t in tiers if notice_hours >= t["min_notice_hours"])
    upper = next((t["min_notice_hours"] for t in reversed(tiers)
                  if t["min_notice_hours"] > tier["min_notice_hours"]), None)
    tier_name = (f"{tier['min_notice_hours']} h+" if upper is None
                 else f"{tier['min_notice_hours']}–{upper} h")
    return {
        "item": item,
        "notice_hours": round(notice_hours, 1),
        "tier": tier_name,
        "amount": tier["amount"],
        "explanation": f"{notice_hours:g} h notice → {tier_name} tier → ${tier['amount']:,}",
    }
