import json
import math
import re

import pytest
from pathlib import Path

from geography import load_geography, shortest_route, validate_geography
from scripts.build_reach_geography import (
    DEFAULT_SOURCE,
    MOON_ORBIT_BASE,
    MOON_ORBIT_STEP,
    ORBIT_ASPECT,
    ORBIT_BASE,
    ORBIT_STEP,
    ART_SCALE,
    DISPLAY_NAMES,
    ORIGINAL_LOCATIONS,
    FORTRESS_SYSTEM_ID,
    ORBIT_CLEARANCE,
    _orbit_extents,
    STAR_NAMES,
    STELLAR_CLASSES,
    build_geography,
)


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


@pytest.mark.parametrize("cycle", ["self", "pair"])
def test_validate_geography_rejects_parent_cycles(cycle: str) -> None:
    payload = _sample_geography()
    parent = payload["bodies"][0]
    if cycle == "pair":
        payload["bodies"].append({**parent, "id": "jericho_anchorage", "parentBodyId": parent["id"]})
        parent["parentBodyId"] = "jericho_anchorage"
    else:
        parent["parentBodyId"] = parent["id"]

    with pytest.raises(ValueError, match="parent cycle"):
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


def test_frontend_orbit_layout_matches_builder_spacing() -> None:
    page = (Path(__file__).resolve().parent / "jericho-strategium.html").read_text(encoding="utf-8")
    number = r"(\d+(?:\.\d+)?)"
    planet = re.search(rf"let radius = {number} \+ body\.orbitIndex \* {number};", page)
    moon = re.search(rf"radius = {number} \+ body\.orbitIndex \* {number};\n", page[planet.end():])
    aspect = re.search(r"y: centerY \+ Math\.sin\(angle\) \* radius \* ([\d.]+)", page)

    assert (float(planet[1]), float(planet[2])) == (ORBIT_BASE, ORBIT_STEP)
    assert (float(moon[1]), float(moon[2])) == (MOON_ORBIT_BASE, MOON_ORBIT_STEP)
    assert float(aspect[1]) == ORBIT_ASPECT


def test_generated_geography_orbits_never_overlap() -> None:
    geography = load_geography(GEOGRAPHY_PATH)
    systems = geography["systems"]
    extents = _orbit_extents(geography["bodies"])

    for index, a in enumerate(systems):
        for b in systems[index + 1:]:
            separation = math.hypot(a["x"] - b["x"], (a["y"] - b["y"]) / ORBIT_ASPECT)
            assert separation >= extents[a["id"]] + extents[b["id"]] + ORBIT_CLEARANCE, (a["name"], b["name"])


def test_generated_geography_preserves_legacy_anchors_and_separates_fortresses() -> None:
    geography = load_geography(GEOGRAPHY_PATH)

    assert len(geography["sectors"]) == 29
    assert len(geography["systems"]) == 22
    assert len(geography["bodies"]) == 109
    assert {sector["number"] for sector in geography["sectors"]} == set(range(1, 30))
    assert all(sector["status"] == "secure" for sector in geography["sectors"])
    assert {system["name"] for system in geography["systems"]} == {f"{name} System" for name in STAR_NAMES[:22]}
    assert {"Avarax", "Kadaku", "Demerium", "Exul", "Recidious"}.isdisjoint(
        body["name"] for body in geography["bodies"]
    )
    assert {body["name"] for body in geography["bodies"] if body["source"] == "canon"} == {
        "Watch Fortress Erioch" if name == "Erioch" else DISPLAY_NAMES.get(name, name)
        for name in ORIGINAL_LOCATIONS
    }
    nonstars = [body for body in geography["bodies"] if body["kind"] != "star"]
    assert len(nonstars) == 87
    assert [body["name"] for body in nonstars if body["source"] == "homebrew"] == ["Watch Fortress Jericho"]
    assert not any("_companion_" in body["id"] for body in geography["bodies"])
    assert all(
        3 <= len([
            body for body in geography["bodies"]
            if body["systemId"] == system["id"] and body["kind"] != "star"
        ]) <= 5
        for system in geography["systems"]
    )
    stars = [body for body in geography["bodies"] if body["kind"] == "star"]
    assert len(stars) == 22
    assert {body["stellarClass"] for body in stars} == set(STELLAR_CLASSES)
    assert len({body["name"] for body in stars}) == 22
    assert all("Primary" not in body["name"] for body in stars)
    for system in geography["systems"]:
        primary = next(body for body in geography["bodies"] if body["id"] == system["primaryBodyId"])
        assert primary["kind"] == "star"
        assert primary["source"] == "homebrew"
        assert "provisional_names" in system["tags"]
    assert geography["landmarks"]["fortressBodyId"] == "watch_fortress_jericho"
    assert geography["landmarks"]["eriochBodyId"] == "erioch"

    route = shortest_route(geography, FORTRESS_SYSTEM_ID, "erioch")
    assert 1 <= route["baseTransitHours"] <= 7 * 24


def test_fortress_and_erioch_positions_match_the_chart() -> None:
    for geography in (load_geography(GEOGRAPHY_PATH), build_geography(json.loads(DEFAULT_SOURCE.read_text()))):
        sectors = {sector["id"]: sector for sector in geography["sectors"]}
        systems = {system["id"]: system for system in geography["systems"]}
        fortress = systems[FORTRESS_SYSTEM_ID]
        erioch = systems["erioch"]
        assert fortress["sectorId"] == erioch["sectorId"] == "jericho_reach"
        assert math.dist((fortress["x"], fortress["y"]), (erioch["x"], erioch["y"])) >= 400
        assert fortress["x"] < erioch["x"] and fortress["y"] > erioch["y"]
        assert all(system["sectorId"] == "jericho_reach" for system in geography["systems"])
        for system in geography["systems"]:
            assert _point_in_polygon((system["x"], system["y"]), sectors[system["sectorId"]]["boundary"])


def test_complete_orbital_envelopes_stay_inside_jericho_reach() -> None:
    geography = load_geography(GEOGRAPHY_PATH)
    boundary = next(sector["boundary"] for sector in geography["sectors"] if sector["id"] == "jericho_reach")
    extents = _orbit_extents(geography["bodies"])
    for system in geography["systems"]:
        for step in range(64):
            angle = step * math.pi * 2 / 64
            point = (system["x"] + math.cos(angle) * extents[system["id"]], system["y"] + math.sin(angle) * extents[system["id"]] * ORBIT_ASPECT)
            assert _point_in_polygon(point, boundary), system["name"]


def _distance_to_boundary(point: tuple[float, float], polygon: list[list[float]]) -> float:
    best = math.inf
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length))
        best = min(best, math.dist(point, (start[0] + t * dx, start[1] + t * dy)))
    return best


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


def test_generated_sector_boundaries_match_numbered_artwork_without_overlap() -> None:
    from PIL import Image
    import numpy as np

    geography = load_geography(GEOGRAPHY_PATH)
    mask = np.asarray(Image.open(GEOGRAPHY_PATH.parents[1] / "assets" / "web" / "galactic-map" / "sectors.png"))
    chart_boundary = geography["chartBoundary"]
    boundaries = [sector["boundary"] for sector in geography["sectors"]]
    all_x = [point[0] for point in chart_boundary]
    all_y = [point[1] for point in chart_boundary]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    assert len(chart_boundary) > 4
    assert len({point[0] for point in chart_boundary}) > 2
    assert len({point[1] for point in chart_boundary}) > 2

    for x_step in range(1, 40):
        for y_step in range(1, 40):
            point = (
                min_x + (max_x - min_x) * x_step / 40,
                min_y + (max_y - min_y) * y_step / 40,
            )
            if min(_distance_to_boundary(point, boundary) for boundary in boundaries) < 4 * ART_SCALE:
                continue
            pixel_x, pixel_y = int(point[0] / ART_SCALE), int(point[1] / ART_SCALE)
            number = int(mask[pixel_y, pixel_x])
            containing = [sector for sector in geography["sectors"] if _point_in_polygon(point, sector["boundary"])]
            assert [sector["number"] for sector in containing] == ([number] if number else [])
            if number:
                assert _point_in_polygon(point, chart_boundary)


@pytest.mark.parametrize("status", ["contested", "unknown", None])
def test_validate_geography_rejects_invalid_sector_status(status) -> None:
    payload = _sample_geography()
    payload["sectors"][0]["status"] = status
    with pytest.raises(ValueError, match="invalid status"):
        validate_geography(payload)


def test_geography_build_is_deterministic() -> None:
    assert build_geography(json.loads(DEFAULT_SOURCE.read_text())) == load_geography(GEOGRAPHY_PATH)


def test_grouped_routes_use_minimum_authored_transit_cost_without_self_links() -> None:
    geography = load_geography(GEOGRAPHY_PATH)
    body_systems = {body["id"]: body["systemId"] for body in geography["bodies"]}
    routes = {frozenset((route["sourceSystemId"], route["targetSystemId"])): route for route in geography["routes"]}
    pair = frozenset((body_systems["bolgra"], body_systems["erioch"]))
    assert routes[pair]["baseTransitHours"] == 14.8
    assert all(route["sourceSystemId"] != route["targetSystemId"] for route in geography["routes"])
    assert len(routes) == len(geography["routes"])


@pytest.mark.parametrize("classification", ["Q", "", None, []])
def test_validate_geography_rejects_invalid_stellar_classes(classification) -> None:
    payload = _sample_geography()
    payload["bodies"][0]["kind"] = "star"
    payload["bodies"][0]["stellarClass"] = classification
    with pytest.raises(ValueError, match="invalid stellarClass"):
        validate_geography(payload)


def test_validate_geography_preserves_stellar_class_and_rejects_it_on_planets() -> None:
    payload = _sample_geography()
    payload["bodies"][0]["kind"] = "star"
    payload["bodies"][0]["stellarClass"] = "F"
    assert validate_geography(payload)["bodies"][0]["stellarClass"] == "F"
    payload["bodies"][0]["kind"] = "agri_world"
    with pytest.raises(ValueError, match="invalid stellarClass"):
        validate_geography(payload)


@pytest.mark.parametrize("number", [0, 30, True, 1.5])
def test_validate_geography_rejects_invalid_sector_numbers(number) -> None:
    payload = _sample_geography()
    payload["sectors"][0]["number"] = number
    with pytest.raises(ValueError, match="invalid or duplicate number"):
        validate_geography(payload)


def test_validate_geography_rejects_duplicate_sector_numbers() -> None:
    payload = _sample_geography()
    payload["sectors"][0]["number"] = 1
    payload["sectors"].append({**payload["sectors"][0], "id": "another_sector"})
    with pytest.raises(ValueError, match="invalid or duplicate number"):
        validate_geography(payload)


def test_validate_geography_preserves_artwork_frame_and_rejects_bad_scale() -> None:
    payload = _sample_geography()
    payload["mapArt"] = {"x": 0, "y": 0, "scale": 5, "width": 1672, "height": 941}
    assert validate_geography(payload)["mapArt"] == payload["mapArt"]
    payload["mapArt"]["scale"] = 0
    with pytest.raises(ValueError, match="mapArt.scale"):
        validate_geography(payload)


def test_status_overlays_reconstruct_sources_and_compose() -> None:
    from PIL import Image
    import numpy as np

    root = GEOGRAPHY_PATH.parents[1] / "assets"
    output = root / "web" / "galactic-map"
    base = Image.open(output / "base.webp").convert("RGBA")
    mask = np.asarray(Image.open(output / "sectors.png"))
    geometry = json.loads((output / "geometry.json").read_text())
    for sector in geometry["sectors"]:
        number = sector["number"]
        label_x, label_y = sector["label"]
        assert mask[label_y, label_x] == number
        for status, suffix in (("secure", "SECURE"), ("critical", "CONTESTED"), ("lost", "LOST")):
            layer = Image.open(output / f"{number}-{status}.webp").convert("RGBA")
            original = Image.open(root / f"JERICHO MAP - {suffix}" / f"JERICHO MAP - {suffix}" / f"{number}-{suffix}.png").convert("RGBA")
            assert np.array_equal(np.asarray(Image.alpha_composite(base, layer)), np.asarray(original))
            assert np.count_nonzero(np.asarray(layer)[:, :, 3]) < mask.size // 2
    combined = base.copy()
    for number, status in ((5, "critical"), (9, "critical"), (23, "lost")):
        combined = Image.alpha_composite(combined, Image.open(output / f"{number}-{status}.webp").convert("RGBA"))
    for number, status in ((5, "critical"), (9, "critical"), (23, "lost")):
        layer = np.asarray(Image.open(output / f"{number}-{status}.webp").convert("RGBA"))
        interior = (mask == number) & (layer[:, :, 3] != 0)
        assert np.array_equal(np.asarray(combined)[interior], layer[interior])