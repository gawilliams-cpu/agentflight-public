from agentflight.tools.reference import (find_trips_for_sender, get_fleet, get_rate_card, get_schedule,
                                         get_trip, get_vendors)


def test_assistant_matches_both_upcoming_client_a_trips(state):
    # Case 7 / A6 depends on this: the assistant could mean either trip.
    result = find_trips_for_sender(state, "Morgan.Pryce@haldenridge.example.com")
    assert result["sender"]["kind"] == "client"
    assert [t["trip"] for t in result["upcoming_trips"]] == ["A1", "A2"]


def test_vendor_sender_is_identified(state):
    result = find_trips_for_sender(state, "concierge@palmettocoast.example.com")
    assert result["sender"]["kind"] == "vendor"
    assert "A1" in [t["trip"] for t in result["upcoming_trips"]]


def test_finished_trips_are_not_upcoming(state):
    # The clock is Tue 09:00. Client B's Monday trip is over; Client G's
    # return leg departs Tue 14:00, so that trip is still upcoming.
    assert find_trips_for_sender(state, "jamie.corrigan@corriganmarine.example.com")["upcoming_trips"] == []
    assert [t["trip"] for t in find_trips_for_sender(
        state, "drew.kaplan@kestrellane.example.com")["upcoming_trips"]] == ["G"]


def test_unknown_sender(state):
    result = find_trips_for_sender(state, "someone@elsewhere.example.com")
    assert result["sender"]["kind"] == "unknown"
    assert result["upcoming_trips"] == []


def test_get_trip_spells_out_the_demo_trip(state):
    trip = get_trip(state, "A1")
    leg_2 = trip["legs"][1]
    assert (leg_2["departure"], leg_2["arrival"]) == ("2026-10-29T16:00", "2026-10-29T18:43")
    assert leg_2["passenger_count"] == 5
    assert leg_2["aircraft"]["tail"] == "N307LH"
    car = next(s for s in trip["services"] if s["type"] == "car_service")
    assert car["how"] == "arranged_by_fbo"
    assert car["vendor_name"] == "Palmetto Coast Aviation"


def test_get_schedule_thursday(state):
    legs = get_schedule(state, "2026-10-29", "2026-10-29")["legs"]
    assert [leg["id"] for leg in legs] == ["H-1", "A1-1", "H-2", "A1-2"]


def test_get_vendors_filters_by_service(state):
    vendors = get_vendors(state, "BED", "catering")["vendors"]
    assert [v["id"] for v in vendors] == ["cat-bed"]
    assert get_vendors(state, "BED", "massage")["vendors"] == []


def test_rate_card(state):
    assert get_rate_card(state)["fixed_charges"]["crew_swap"]["amount"] == 2500


def test_get_fleet_lists_every_aircraft_with_seats(state):
    fleet = {a["tail"]: a for a in get_fleet(state)["aircraft"]}
    assert fleet["N350LH"]["seats"] == 9 and fleet["N350LH"]["base"] == "BED"
    assert fleet["N560LH"]["seats"] == 8
    assert len(fleet) == 3



def test_reference_view_has_photos_pilots_and_favorite_airports(state):
    from agentflight.views import reference
    view = reference(state)
    phenom = next(a for a in view["aircraft"] if a["tail"] == "N307LH")
    assert [p["caption"] for p in phenom["photos"]] == ["Exterior", "Cabin"]
    assert all(p["credit"] and p["license"] for a in view["aircraft"] for p in a["photos"])
    dana = next(p for p in view["pilots"] if p["name"] == "Dana Whitlock")
    assert dana["flies_with"] == ["Marcus Reyes"] and dana["rating"] == "Phenom 300"
    assert len(view["pilots"]) == 2 * len(state["crew"])
    bed = next(a for a in view["airports"] if a["code"] == "BED")
    assert bed["favorite"] and bed["approaches"] and bed["map"]["tiles"] and bed["vendors"]


def test_reference_view_lists_each_clients_people_and_next_trip(state):
    from agentflight.views import reference
    clients = {c["id"]: c for c in reference(state)["clients"]}
    halden = clients["A"]
    assert halden["trip_count"] == 2 and halden["next_trip"] == "A1"
    assert [p["name"] for p in halden["people"] if p["contact"]] == ["Alex Halden", "Morgan Pryce"]
    assert all(p["phone"] and p["passport"]["number"] for p in halden["people"])
    # Every passenger on a trip is listed under that trip's client.
    for trip in state["trips"]:
        names = {p["name"] for p in clients[trip["client"]]["people"]}
        assert {name for leg in trip["legs"] for name in leg["passengers"]} <= names
    assert clients["F"]["next_trip"] is None
