#!/usr/bin/env python3
"""Build orbital systems around the original Jericho Reach locations."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geography import validate_geography

DEFAULT_SOURCE = ROOT.parent / "discord-bots" / "op-scribe-servitor" / "reference" / "jericho_reach_graph.json"
DEFAULT_OUTPUT = ROOT / "data" / "reach_geography.json"
MAP_ASSETS = ROOT / "assets" / "web" / "galactic-map"
ART_SCALE = 5.0
ORBIT_BASE = 5.0
ORBIT_STEP = 4.0
MOON_ORBIT_BASE = 2.0
MOON_ORBIT_STEP = 1.1
ORBIT_ASPECT = .46
ORBIT_CLEARANCE = 6.0
FORTRESS_BODY_ID = "watch_fortress_jericho"
FORTRESS_SYSTEM_ID = "watch_fortress_jericho_system"
STAR_NAMES = (
    "Vigil Lux", "Vespera", "Cineris", "Asterion", "Solenne", "Nacreus", "Velorian",
    "Caelus", "Orison", "Aurelis", "Sidera", "Tenebris", "Valestrum", "Luminara",
    "Caldris", "Noctivar", "Solis Votum", "Ferrum Lux", "Meridian", "Aster Vale",
    "Candescent", "Penumbris", "Votive Dawn", "Hesperis", "Astralis", "Lucentis",
    "Serenith", "Veyra", "Altaris", "Sable Light",
)
STELLAR_CLASSES = ("O", "B", "A", "F", "G", "K", "M")

ORIGINAL_LOCATIONS = frozenset({
    "Phaegis", "The Warp Gate", "Hethgard", "Alphos", "Pyrathas", "Calisi", "Karlak",
    "Spite", "Carmyn", "Eleusis", "Aurum", "Castolel", "Freya", "Vanir", "Herisor",
    "Atonement", "Phonos", "Arkhas", "Beseritor", "Jove's Descent", "Zanatov's Harbor",
    "Jerober XI", "The Blood Trinity", "Vanity", "Khazant", "Magog", "Bolgra",
    "Hlesan Secundus", "Samech", "Malehi", "Midsel", "Ormasim", "Credence", "Kaggeran",
    "Argoth", "Rheelas", "Hestus", "Vespasia", "Bellom", "Resgulus", "Wrath",
    "Scansion Beta", "Pelegius", "Themiskon Point", "Meskaile", "Meniscus", "Bekrin",
    "Oertha", "Baraban", "Dakinor", "Veren", "Ravacene", "Tabius Rasa", "Ries", "Iobel",
    "Erioch", "Melancholia", "Vormos", "Pellor", "Andronicus", "Zurcon", "Iphigenia",
    "Krk'tikit", "Tsua'Malor", "Skapula", "Klaha", "Hector's Endeavour", "Kabiri",
    "Sagacity", "Cosel", "Ynnen", "Octavian", "Nunc", "Polyphemos", "Sovereign",
    "Castiel", "Credos", "Sedu", "Lovat IV", "Pilgrim's Loss", "Mackensee", "Mahir",
    "Vathor", "Cressid", "Falon's Lament", "Belissar",
})
DISPLAY_NAMES = {"Castolel": "Castobel"}
LEGACY_REGIONS = {"hadex": "hadex_anomaly", "quarantined": "canis_salient", "slinnar_drift": "outer_reach", "contested": "coreward_marches"}
WORLD_REGIONS = {
    **dict.fromkeys(("Bekrin", "Baraban", "Dakinor", "Veren", "Ravacene"), "greyhell_front"),
    **dict.fromkeys(("Zurcon", "Iphigenia"), "black_reef"),
    **dict.fromkeys(("Tabius Rasa", "Ries", "Iobel", "Melancholia", "Castiel", "Nunc", "Octavian"), "cellebos_warzone"),
    "Polyphemos": "outer_reach",
    "Erioch": "hadex_anomaly",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:80]


def _point(point: list[float]) -> list[float]:
    return [round(coordinate * ART_SCALE, 3) for coordinate in point]


def _orbit_extents(bodies: list[dict[str, Any]]) -> dict[str, float]:
    by_id = {body["id"]: body for body in bodies}

    def extent(body: dict[str, Any]) -> float:
        if body["kind"] == "star":
            return 0.0
        if body.get("parentBodyId"):
            return extent(by_id[body["parentBodyId"]]) + MOON_ORBIT_BASE + body["orbitIndex"] * MOON_ORBIT_STEP
        return ORBIT_BASE + body["orbitIndex"] * ORBIT_STEP

    result = {}
    for body in bodies:
        result[body["systemId"]] = max(result.get(body["systemId"], 0), extent(body))
    return result


def _cluster_locations(locations: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    remaining = {location["id"]: location for location in locations}
    groups = []
    while remaining:
        anchor_id = next((item for item in ("erioch", FORTRESS_SYSTEM_ID) if item in remaining), None)
        anchor = remaining[anchor_id] if anchor_id else min(remaining.values(), key=lambda item: (item["x"], item["y"], item["id"]))
        count = 5 if len(remaining) in {5, 9} else 4 if len(remaining) >= 7 else 3
        group = [anchor]
        remaining.pop(anchor["id"])
        while len(group) < count and remaining:
            center_x = sum(item["x"] for item in group) / len(group)
            center_y = sum(item["y"] for item in group) / len(group)
            candidates = [item for item in remaining.values() if item["id"] not in {"erioch", FORTRESS_SYSTEM_ID}]
            if not candidates:
                raise ValueError("Cannot group fortress locations separately")
            closest = min(candidates, key=lambda item: ((item["x"] - center_x) ** 2 + (item["y"] - center_y) ** 2, item["id"]))
            group.append(closest)
            remaining.pop(closest["id"])
        groups.append(group)
    if any(not 3 <= len(group) <= 5 for group in groups):
        raise ValueError("Canonical groups must contain three to five existing locations")
    return groups


def _place_orbital_systems(systems: list[dict[str, Any]], bodies: list[dict[str, Any]], depth: Any) -> None:
    extents = _orbit_extents(bodies)
    available_y, available_x = np.nonzero(depth >= 2)
    settled = []
    by_id = {system["id"]: system for system in systems}
    ordered = [by_id["erioch"], by_id[FORTRESS_SYSTEM_ID]] + [system for system in systems if system["id"] not in {"erioch", FORTRESS_SYSTEM_ID}]
    center_y, center_x = np.unravel_index(depth.argmax(), depth.shape)
    reach_radius = float(depth.max())
    fortress_targets = {
        "erioch": (center_x + reach_radius * .58, center_y - reach_radius * .38),
        FORTRESS_SYSTEM_ID: (center_x - reach_radius * .58, center_y + reach_radius * .38),
    }
    for system in ordered:
        radius = extents[system["id"]] / ART_SCALE
        allowed = depth[available_y, available_x] >= radius + 2
        for other in settled:
            distance = np.hypot(available_x - other["x"] / ART_SCALE, (available_y - other["y"] / ART_SCALE) / ORBIT_ASPECT)
            allowed &= distance >= radius + (extents[other["id"]] + ORBIT_CLEARANCE) / ART_SCALE
        target_x, target_y = fortress_targets.get(system["id"], (system["x"] / ART_SCALE, system["y"] / ART_SCALE))
        distances = np.where(allowed, (available_x - target_x) ** 2 + (available_y - target_y) ** 2, np.inf)
        best = int(distances.argmin())
        if not np.isfinite(distances[best]):
            raise ValueError(f"No non-overlapping orbital placement for {system['name']}")
        system["x"], system["y"] = int(available_x[best]) * ART_SCALE, int(available_y[best]) * ART_SCALE
        settled.append(system)


def build_geography(graph: dict[str, Any]) -> dict[str, Any]:
    geometry = json.loads((MAP_ASSETS / "geometry.json").read_text(encoding="utf-8"))
    mask = np.asarray(Image.open(MAP_ASSETS / "sectors.png"))
    regions = geometry["sectors"]
    sectors = [{
        "id": "jericho_reach" if region["number"] == 1 else f"sector_{region['number']:02d}",
        "number": region["number"],
        "name": "Jericho Reach" if region["number"] == 1 else f"Sector {region['number']:02d}",
        "status": "secure", "boundary": [_point(point) for point in region["boundary"]],
        "label": _point(region["label"]),
    } for region in regions]
    nodes = [node for node in graph["nodes"] if node["id"] in ORIGINAL_LOCATIONS]
    if {node["id"] for node in nodes} != ORIGINAL_LOCATIONS:
        raise ValueError("Original artwork location inventory is incomplete")
    nodes.append({"id": "Watch Fortress Jericho", "type": "watch_fortress", "x": 625, "y": 610, "region": "outer_reach"})
    node_ids = {node["id"]: _slug(node["id"]) for node in nodes}
    node_ids["Watch Fortress Jericho"] = FORTRESS_SYSTEM_ID
    depth = cv2.distanceTransform((mask == 1).astype(np.uint8), cv2.DIST_L2, 5)
    available_y, available_x = np.nonzero(depth >= 5)
    available = np.ones(len(available_x), dtype=bool)
    center_x, center_y = regions[0]["label"]
    radius = float(depth.max()) - 8
    min_x, max_x = min(node["x"] for node in nodes), max(node["x"] for node in nodes)
    min_y, max_y = min(node["y"] for node in nodes), max(node["y"] for node in nodes)
    systems, bodies = [], []
    for node in nodes:
        normalized_x = (node["x"] - min_x) / (max_x - min_x) * 2 - 1
        normalized_y = (node["y"] - min_y) / (max_y - min_y) * 2 - 1
        target_x = center_x + radius * normalized_x * math.sqrt(1 - normalized_y ** 2 / 2)
        target_y = center_y + radius * normalized_y * math.sqrt(1 - normalized_x ** 2 / 2)
        distances = (available_x - target_x) ** 2 + (available_y - target_y) ** 2
        distances = np.where(available, distances, np.inf)
        best = int(distances.argmin())
        if not np.isfinite(distances[best]):
            raise ValueError("Jericho Reach has insufficient room for its original locations")
        pixel_x, pixel_y = int(available_x[best]), int(available_y[best])
        available[(available_x - pixel_x) ** 2 + (available_y - pixel_y) ** 2 < 9 ** 2] = False
        name, system_id = node["id"], node_ids[node["id"]]
        body_id = FORTRESS_BODY_ID if name == "Watch Fortress Jericho" else system_id
        is_fortress = name in {"Erioch", "Watch Fortress Jericho"}
        source = "homebrew" if name == "Watch Fortress Jericho" else "canon"
        display_name = DISPLAY_NAMES.get(name, name)
        kind = "watch_fortress" if is_fortress else "death_world" if name == "Herisor" else node["type"]
        systems.append({
            "id": system_id, "name": f"{display_name} System", "sectorId": "jericho_reach",
            "x": pixel_x * ART_SCALE, "y": pixel_y * ART_SCALE,
            "classification": "watch_fortress_system" if is_fortress else "charted_system",
            "primaryBodyId": f"{system_id}_star", "source": source,
            "tags": (["fortress"] if name == "Watch Fortress Jericho" else ["watch_station"] if name == "Erioch" else []) + ["modeled_system", "provisional_names"],
        })
        seed = sum((index + 1) * ord(character) for index, character in enumerate(system_id))
        bodies.append({
            "id": body_id, "systemId": system_id, "parentBodyId": None,
            "name": "Watch Fortress Erioch" if name == "Erioch" else display_name,
            "kind": kind, "orbitIndex": 1, "orbitAngle": seed % 360,
            "displayRadius": 7 if is_fortress else 5,
            "battleEligible": not is_fortress and kind != "special", "source": source,
        })
    _place_orbital_systems(systems, bodies, depth)
    groups = _cluster_locations(systems)
    if len(groups) > len(STAR_NAMES):
        raise ValueError("Not enough provisional star names for canonical groups")
    bodies_by_location = {body["systemId"]: body for body in bodies}
    grouped_systems = []
    location_to_system = {}
    for index, group in enumerate(groups):
        anchor = group[0]
        system_id = anchor["id"]
        star_name = STAR_NAMES[index]
        center_x = sum(location["x"] for location in group) / len(group)
        center_y = sum(location["y"] for location in group) / len(group)
        grouped_systems.append({
            "id": system_id, "name": f"{star_name} System", "sectorId": "jericho_reach",
            "x": center_x, "y": center_y, "classification": "charted_system",
            "primaryBodyId": f"{system_id}_star", "source": "homebrew",
            "tags": ["modeled_system", "provisional_names"] + (["fortress"] if system_id == FORTRESS_SYSTEM_ID else ["watch_station"] if system_id == "erioch" else []),
        })
        bodies.append({
            "id": f"{system_id}_star", "systemId": system_id, "parentBodyId": None,
            "name": star_name, "kind": "star", "stellarClass": STELLAR_CLASSES[index % len(STELLAR_CLASSES)],
            "orbitIndex": 0, "orbitAngle": 0, "displayRadius": 6, "battleEligible": False, "source": "homebrew",
        })
        ordered = sorted(group, key=lambda location: ((location["x"] - center_x) ** 2 + ((location["y"] - center_y) / ORBIT_ASPECT) ** 2, location["id"]))
        for orbit_index, location in enumerate(ordered, start=1):
            body = bodies_by_location[location["id"]]
            body["systemId"] = system_id
            body["orbitIndex"] = orbit_index
            body["orbitAngle"] = math.degrees(math.atan2((location["y"] - center_y) / ORBIT_ASPECT, location["x"] - center_x)) % 360
            location_to_system[location["id"]] = system_id
    systems = grouped_systems
    _place_orbital_systems(systems, bodies, depth)
    node_ids = {name: location_to_system[location_id] for name, location_id in node_ids.items()}
    old_regions = {node["id"]: WORLD_REGIONS.get(node["id"], LEGACY_REGIONS.get(node["region"], node["region"])) for node in nodes}
    routes_by_pair = {}
    for edge in graph["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            continue
        source_id, target_id = node_ids[edge["source"]], node_ids[edge["target"]]
        if source_id == target_id:
            continue
        pair = tuple(sorted((source_id, target_id)))
        same_region = old_regions[edge["source"]] == old_regions[edge["target"]]
        proximity, distance = edge.get("proximity", "medium"), float(edge.get("distance", 100))
        hours = max(6, min(24, distance / 8)) if same_region else max(24, min(72, distance / 3))
        if proximity == "far":
            hours = max(hours, 72)
        route = {"id": "__".join(pair), "sourceSystemId": source_id, "targetSystemId": target_id,
                 "routeType": "charted_warp", "baseTransitHours": round(hours, 1),
                 "risk": "high" if proximity == "far" else "moderate" if proximity == "medium" or not same_region else "low", "status": "open"}
        if pair not in routes_by_pair or hours < routes_by_pair[pair]["baseTransitHours"]:
            routes_by_pair[pair] = route
    for target_name, hours, risk in (("Tsua'Malor", 30, "moderate"), ("Skapula", 42, "high"), ("Sagacity", 54, "high")):
        target_id = node_ids[target_name]
        if target_id == FORTRESS_SYSTEM_ID:
            continue
        pair = tuple(sorted((FORTRESS_SYSTEM_ID, target_id)))
        if pair not in routes_by_pair or hours < routes_by_pair[pair]["baseTransitHours"]:
            routes_by_pair[pair] = {"id": "__".join(pair), "sourceSystemId": FORTRESS_SYSTEM_ID,
                                   "targetSystemId": target_id, "routeType": "fortress_corridor",
                                   "baseTransitHours": hours, "risk": risk, "status": "open"}
    chart = cv2.dilate((mask != 0).astype(np.uint8), np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(chart, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boundary = cv2.approxPolyDP(max(contours, key=cv2.contourArea), .75, True)[:, 0, :].tolist()
    return validate_geography({
        "schemaVersion": 1, "mapArt": {"x": 0, "y": 0, "scale": ART_SCALE, "width": geometry["width"], "height": geometry["height"]},
        "chartBoundary": [_point(point) for point in boundary], "sectors": sectors, "subregions": [],
        "systems": systems, "bodies": bodies, "routes": list(routes_by_pair.values()),
        "landmarks": {"fortressBodyId": FORTRESS_BODY_ID, "eriochBodyId": "erioch"},
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    geography = build_geography(json.loads(args.source.read_text(encoding="utf-8")))
    print(f"Validated {len(geography['sectors'])} sectors, {len(geography['systems'])} systems, {len(geography['bodies'])} bodies and {len(geography['routes'])} routes.")
    if args.write:
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(geography, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.output)
    else:
        print("Dry run only. Re-run with --write to create the geography file.")


if __name__ == "__main__":
    main()