import pytest
from pathlib import Path

from geography import load_geography, shortest_route, validate_geography


GEOGRAPHY_PATH = Path(__file__).resolve().parent / "data" / "reach_geography.json"


def _sample_geography() -> dict:
    return {
        "schemaVersion": 1,
        "chartBoundary": [[-20, 0], [80, -20], [120, 50], [100, 120], [0, 100]],
        "sectors": [
            {
                "id": "canis_salient",
                "name": "Canis Salient",
                "boundary": [[0, 0], [100, 0], [100, 100], [0, 100]],
                "label": [50, 50],
            }
        ],
        "subregions": [],
        "systems": [
            {
                "id": "jericho_bastion",
                "name": "Jericho Bastion",
                "sectorId": "canis_salient",
                "x": 10,
                "y": 80,
                "classification": "watch_fortress_system",
                "primaryBodyId": "watch_fortress_jericho",
                "source": "homebrew",
                "tags": ["fortress"],
            },
            {
                "id": "erioch",
                "name": "Erioch",
                "sectorId": "canis_salient",
                "x": 80,
                "y": 20,
                "classification": "watch_station_system",
                "primaryBodyId": "erioch_station",
                "source": "local_reference",
                "tags": ["watch_station"],
            },
        ],
        "bodies": [
            {
                "id": "watch_fortress_jericho",
                "systemId": "jericho_bastion",
                "parentBodyId": None,
                "name": "Watch Fortress Jericho",
                "kind": "watch_fortress",
                "orbitIndex": 1,
                "orbitAngle": 220,
                "displayRadius": 8,
                "battleEligible": False,
                "source": "homebrew",
            },
            {
                "id": "erioch_station",
                "systemId": "erioch",
                "parentBodyId": None,
                "name": "Erioch",
                "kind": "watch_station",
                "orbitIndex": 1,
                "orbitAngle": 30,
                "displayRadius": 7,
                "battleEligible": False,
                "source": "local_reference",
            },
        ],
        "routes": [
            {
                "id": "jericho_bastion__erioch",
                "sourceSystemId": "jericho_bastion",
                "targetSystemId": "erioch",
                "routeType": "charted_warp",
                "baseTransitHours": 72,
                "risk": "high",
                "status": "open",
            }
        ],
        "landmarks": {
            "fortressBodyId": "watch_fortress_jericho",
            "eriochBodyId": "erioch_station",
        },
    }


def test_validate_geography_preserves_distinct_fortress_landmarks() -> None:
    geography = validate_geography(_sample_geography())

    assert geography["landmarks"] == {
        "fortressBodyId": "watch_fortress_jericho",
        "eriochBodyId": "erioch_station",
    }
    assert geography["landmarks"]["fortressBodyId"] != geography["landmarks"]["eriochBodyId"]


def test_validate_geography_rejects_orphan_body() -> None:
    payload = _sample_geography()
    payload["bodies"][0]["systemId"] = "missing"

    with pytest.raises(ValueError, match="unknown system"):
        validate_geography(payload)


def test_shortest_route_uses_authored_transit_hours() -> None:
    geography = validate_geography(_sample_geography())

    route = shortest_route(geography, "jericho_bastion", "erioch")

    assert route["systemIds"] == ["jericho_bastion", "erioch"]
    assert route["routeIds"] == ["jericho_bastion__erioch"]
    assert route["baseTransitHours"] == 72


def test_shortest_route_ignores_closed_routes() -> None:
    payload = _sample_geography()
    payload["routes"][0]["status"] = "closed"
    geography = validate_geography(payload)

    with pytest.raises(ValueError, match="No open route"):
        shortest_route(geography, "jericho_bastion", "erioch")


def test_generated_geography_preserves_legacy_anchors_and_separates_fortresses() -> None:
    geography = load_geography(GEOGRAPHY_PATH)

    assert len(geography["sectors"]) == 7
    assert len(geography["systems"]) == 90
    assert 360 <= len(geography["bodies"]) <= 540
    assert {system["name"] for system in geography["systems"]} >= {
        "Recidious",
        "Erioch",
        "Jericho Bastion",
    }
    assert {"Avarax", "Kadaku", "Demerium"}.isdisjoint(
        system["name"] for system in geography["systems"]
    )
    recidious = next(system for system in geography["systems"] if system["id"] == "recidious")
    recidious_bodies = {body["name"]: body for body in geography["bodies"] if body["systemId"] == "recidious"}
    assert recidious["primaryBodyId"] == "recidious_star"
    assert recidious_bodies["Recidious Primary"]["kind"] == "star"
    assert {"Kadaku", "Avarax", "Demerium"} <= set(recidious_bodies)
    assert all(
        next(body for body in geography["bodies"] if body["id"] == system["primaryBodyId"])["kind"] == "star"
        for system in geography["systems"]
    )
    assert all(
        3 <= len([
            body for body in geography["bodies"]
            if body["systemId"] == system["id"] and body["kind"] != "star"
        ]) <= 5
        for system in geography["systems"]
    )
    body_kinds = {body["kind"] for body in geography["bodies"]}
    assert {"moon", "asteroid_belt", "orbital_station", "space_hulk"} <= body_kinds
    assert geography["landmarks"]["fortressBodyId"] == "watch_fortress_jericho"
    assert geography["landmarks"]["eriochBodyId"] == "erioch"

    route = shortest_route(geography, "jericho_bastion", "erioch")
    assert 48 <= route["baseTransitHours"] <= 7 * 24


def _point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        crosses = (current_y > y) != (previous_y > y)
        if crosses:
            boundary_x = (previous_x - current_x) * (y - current_y) / (previous_y - current_y) + current_x
            if x < boundary_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def test_generated_sector_boundaries_form_one_non_overlapping_partition() -> None:
    geography = load_geography(GEOGRAPHY_PATH)
    chart_boundary = geography["chartBoundary"]
    boundaries = [sector["boundary"] for sector in geography["sectors"]]
    all_x = [point[0] for point in chart_boundary]
    all_y = [point[1] for point in chart_boundary]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    assert len(chart_boundary) > 4
    assert len({point[0] for point in chart_boundary}) > 2
    assert len({point[1] for point in chart_boundary}) > 2

    for x_step in range(1, 20):
        for y_step in range(1, 20):
            point = (
                min_x + (max_x - min_x) * x_step / 20,
                min_y + (max_y - min_y) * y_step / 20,
            )
            expected = 1 if _point_in_polygon(point, chart_boundary) else 0
            assert sum(_point_in_polygon(point, boundary) for boundary in boundaries) == expected