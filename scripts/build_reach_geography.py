#!/usr/bin/env python3
"""Build the Strategium's hierarchical geography from the legacy Reach graph."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geography import shortest_route, validate_geography  # noqa: E402

DEFAULT_SOURCE = ROOT.parent / "discord-bots" / "op-scribe-servitor" / "reference" / "jericho_reach_graph.json"
DEFAULT_OUTPUT = ROOT / "data" / "reach_geography.json"

SECTOR_NAMES = {
    "iron_collar": "Iron Collar",
    "acheros_salient": "Acheros Salient",
    "orpheus_salient": "Orpheus Salient",
    "canis_salient": "Canis Salient",
    "cellebos_warzone": "Cellebos Warzone",
    "hadex_anomaly": "Hadex Anomaly",
    "slinnar_drift": "Slinnar Drift",
}

MAP_X_SCALE = 1.75

_SECTOR_SITES = {
    "iron_collar": (165.0, 120.0),
    "acheros_salient": (235.0, 335.0),
    "orpheus_salient": (690.0, 175.0),
    "canis_salient": (300.0, 705.0),
    "cellebos_warzone": (470.0, 330.0),
    "hadex_anomaly": (610.0, 500.0),
    "slinnar_drift": (980.0, 545.0),
}
SECTOR_SITES = {
    sector_id: (x * MAP_X_SCALE, y)
    for sector_id, (x, y) in _SECTOR_SITES.items()
}

_CHART_BOUNDARY = [
    (-40.0, 170.0),
    (45.0, 45.0),
    (270.0, 10.0),
    (520.0, 45.0),
    (760.0, 20.0),
    (1040.0, 75.0),
    (1220.0, 230.0),
    (1200.0, 520.0),
    (1240.0, 760.0),
    (1060.0, 930.0),
    (780.0, 965.0),
    (545.0, 1015.0),
    (300.0, 980.0),
    (70.0, 835.0),
    (-35.0, 600.0),
    (15.0, 390.0),
]
CHART_BOUNDARY = [(x * MAP_X_SCALE, y) for x, y in _CHART_BOUNDARY]

DIRECT_SECTOR = {
    "iron_collar": "iron_collar",
    "acheros_salient": "acheros_salient",
    "orpheus_salient": "orpheus_salient",
    "canis_salient": "canis_salient",
    "cellebos_warzone": "cellebos_warzone",
    "hadex": "hadex_anomaly",
    "slinnar_drift": "slinnar_drift",
    "quarantined": "acheros_salient",
    "black_reef": "canis_salient",
}

FORTRESS_SYSTEM_ID = "jericho_bastion"
FORTRESS_BODY_ID = "watch_fortress_jericho"
ERIOCH_BODY_ID = "erioch"
RECIDIOUS_SYSTEM_ID = "recidious"
RECIDIOUS_PLANETS = ("Kadaku", "Avarax", "Demerium")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return slug[:80]


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _clip_to_nearest_site(
    polygon: list[tuple[float, float]],
    site: tuple[float, float],
    other: tuple[float, float],
) -> list[tuple[float, float]]:
    coefficient_x = 2 * (other[0] - site[0])
    coefficient_y = 2 * (other[1] - site[1])
    limit = other[0] ** 2 + other[1] ** 2 - site[0] ** 2 - site[1] ** 2

    def signed_distance(point: tuple[float, float]) -> float:
        return coefficient_x * point[0] + coefficient_y * point[1] - limit

    clipped: list[tuple[float, float]] = []
    previous = polygon[-1]
    previous_distance = signed_distance(previous)
    previous_inside = previous_distance <= 1e-7
    for current in polygon:
        current_distance = signed_distance(current)
        current_inside = current_distance <= 1e-7
        if current_inside != previous_inside:
            ratio = previous_distance / (previous_distance - current_distance)
            clipped.append((
                previous[0] + (current[0] - previous[0]) * ratio,
                previous[1] + (current[1] - previous[1]) * ratio,
            ))
        if current_inside:
            clipped.append(current)
        previous = current
        previous_distance = current_distance
        previous_inside = current_inside
    return clipped


def _sector_partitions() -> dict[str, list[list[float]]]:
    partitions: dict[str, list[list[float]]] = {}
    for sector_id, site in SECTOR_SITES.items():
        polygon = CHART_BOUNDARY
        for other_id, other_site in SECTOR_SITES.items():
            if other_id != sector_id:
                polygon = _clip_to_nearest_site(polygon, site, other_site)
        partitions[sector_id] = [[round(x, 3), round(y, 3)] for x, y in polygon]
    return partitions


def _normalized_sector(node: dict[str, Any], centroids: dict[str, tuple[float, float]]) -> str:
    region = str(node.get("region") or "")
    if region != "contested":
        return DIRECT_SECTOR[region]
    x, y = float(node["x"]), float(node["y"])
    candidates = ("acheros_salient", "canis_salient", "hadex_anomaly", "slinnar_drift")
    return min(candidates, key=lambda sector_id: math.dist((x, y), centroids[sector_id]))


def _route_hours(distance: float, same_sector: bool, proximity: str) -> float:
    if same_sector:
        hours = max(6.0, min(24.0, distance / 8.0))
    else:
        hours = max(24.0, min(72.0, distance / 3.0))
    if proximity == "far":
        hours = max(hours, 72.0)
    return round(hours, 1)


def _route_risk(proximity: str, same_sector: bool) -> str:
    if proximity == "far":
        return "high"
    if proximity == "medium" or not same_sector:
        return "moderate"
    return "low"


def build_geography(graph: dict[str, Any]) -> dict[str, Any]:
    legacy_nodes = list(graph.get("nodes") or [])
    legacy_edges = list(graph.get("edges") or [])
    if not legacy_nodes:
        raise ValueError("legacy graph contains no nodes")

    direct_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for node in legacy_nodes:
        region = str(node.get("region") or "")
        sector_id = DIRECT_SECTOR.get(region)
        if sector_id:
            direct_points[sector_id].append((float(node["x"]), float(node["y"])))
    centroids = {sector_id: _centroid(points) for sector_id, points in direct_points.items()}

    id_by_name: dict[str, str] = {name: RECIDIOUS_SYSTEM_ID for name in RECIDIOUS_PLANETS}
    used_ids: set[str] = {RECIDIOUS_SYSTEM_ID}
    for node in legacy_nodes:
        if str(node["id"]) in RECIDIOUS_PLANETS:
            continue
        base = _slug(str(node["id"]))
        system_id = base
        suffix = 2
        while system_id in used_ids:
            system_id = f"{base}_{suffix}"
            suffix += 1
        used_ids.add(system_id)
        id_by_name[str(node["id"])] = system_id

    systems = []
    bodies = []
    original_regions: dict[str, str] = {}
    sector_by_system: dict[str, str] = {}
    for index, node in enumerate(legacy_nodes):
        name = str(node["id"])
        if name in RECIDIOUS_PLANETS:
            continue
        system_id = id_by_name[name]
        sector_id = _normalized_sector(node, centroids)
        original_region = str(node.get("region") or "")
        original_regions[system_id] = original_region
        sector_by_system[system_id] = sector_id
        tags = []
        if node.get("game_planet"):
            tags.append("game_planet")
        if node.get("chaos_tainted"):
            tags.append("chaos_tainted")
        if node.get("type") == "watch_station":
            tags.append("watch_station")
        systems.append({
            "id": system_id,
            "name": name,
            "sectorId": sector_id,
            "x": float(node["x"]) * MAP_X_SCALE,
            "y": float(node["y"]),
            "classification": "watch_station_system" if node.get("type") == "watch_station" else "charted_system",
            "primaryBodyId": f"{system_id}_star",
            "source": "local_reference",
            "tags": tags,
        })
        bodies.append({
            "id": f"{system_id}_star",
            "systemId": system_id,
            "parentBodyId": None,
            "name": f"{name} Primary",
            "kind": "star",
            "orbitIndex": 0,
            "orbitAngle": 0,
            "displayRadius": 6,
            "battleEligible": False,
            "source": "local_reference",
        })
        bodies.append({
            "id": system_id,
            "systemId": system_id,
            "parentBodyId": None,
            "name": name,
            "kind": str(node.get("type") or "dead_world"),
            "orbitIndex": 1,
            "orbitAngle": (index * 137.508) % 360,
            "displayRadius": 7 if node.get("type") == "watch_station" else 5,
            "battleEligible": node.get("type") not in {"special", "watch_station"},
            "source": "local_reference",
        })

    recidious_nodes = {str(node["id"]): node for node in legacy_nodes if str(node["id"]) in RECIDIOUS_PLANETS}
    recidious_x = sum(float(node["x"]) for node in recidious_nodes.values()) / len(recidious_nodes) * MAP_X_SCALE
    recidious_y = sum(float(node["y"]) for node in recidious_nodes.values()) / len(recidious_nodes)
    systems.append({
        "id": RECIDIOUS_SYSTEM_ID,
        "name": "Recidious",
        "sectorId": "acheros_salient",
        "x": recidious_x,
        "y": recidious_y,
        "classification": "charted_system",
        "primaryBodyId": "recidious_star",
        "source": "canon",
        "tags": ["game_planet", "multi_body_system"],
    })
    sector_by_system[RECIDIOUS_SYSTEM_ID] = "acheros_salient"
    original_regions[RECIDIOUS_SYSTEM_ID] = "acheros_salient"
    bodies.append({
        "id": "recidious_star",
        "systemId": RECIDIOUS_SYSTEM_ID,
        "parentBodyId": None,
        "name": "Recidious Primary",
        "kind": "star",
        "orbitIndex": 0,
        "orbitAngle": 0,
        "displayRadius": 7,
        "battleEligible": False,
        "source": "canon",
    })
    for orbit_index, (planet_name, orbit_angle) in enumerate(
        zip(RECIDIOUS_PLANETS, (205, 35, 315)), start=1
    ):
        node = recidious_nodes[planet_name]
        bodies.append({
            "id": _slug(planet_name),
            "systemId": RECIDIOUS_SYSTEM_ID,
            "parentBodyId": None,
            "name": planet_name,
            "kind": str(node.get("type") or "dead_world"),
            "orbitIndex": orbit_index,
            "orbitAngle": orbit_angle,
            "displayRadius": 7 if planet_name == "Avarax" else 6,
            "battleEligible": True,
            "source": "canon",
        })

    systems.append({
        "id": FORTRESS_SYSTEM_ID,
        "name": "Jericho Bastion",
        "sectorId": "canis_salient",
        "x": 500 * MAP_X_SCALE,
        "y": 930,
        "classification": "watch_fortress_system",
        "primaryBodyId": "jericho_bastion_star",
        "source": "homebrew",
        "tags": ["fortress", "logistics_hub"],
    })
    sector_by_system[FORTRESS_SYSTEM_ID] = "canis_salient"
    bodies.extend([
        {
            "id": "jericho_bastion_star",
            "systemId": FORTRESS_SYSTEM_ID,
            "parentBodyId": None,
            "name": "Jericho Bastion Primary",
            "kind": "star",
            "orbitIndex": 0,
            "orbitAngle": 0,
            "displayRadius": 7,
            "battleEligible": False,
            "source": "homebrew",
        },
        {
            "id": FORTRESS_BODY_ID,
            "systemId": FORTRESS_SYSTEM_ID,
            "parentBodyId": None,
            "name": "Watch Fortress Jericho",
            "kind": "watch_fortress",
            "orbitIndex": 1,
            "orbitAngle": 220,
            "displayRadius": 9,
            "battleEligible": False,
            "source": "homebrew",
        },
        {
            "id": "jericho_anchorage",
            "systemId": FORTRESS_SYSTEM_ID,
            "parentBodyId": FORTRESS_BODY_ID,
            "name": "Jericho Anchorage",
            "kind": "void_dock",
            "orbitIndex": 2,
            "orbitAngle": 35,
            "displayRadius": 5,
            "battleEligible": True,
            "source": "homebrew",
        },
    ])

    supplemental_templates = (
        ("Secundus", "moon", True),
        ("Tertius", "moon", True),
        ("Outer Belt", "asteroid_belt", False),
        ("Halo Station", "orbital_station", True),
        ("Drift Hulk", "space_hulk", True),
    )
    for system in systems:
        if system["id"] == RECIDIOUS_SYSTEM_ID:
            continue
        existing = [body for body in bodies if body["systemId"] == system["id"] and body["kind"] != "star"]
        target_count = 3 + sum(ord(character) for character in system["id"]) % 3
        for body_index in range(len(existing), target_count):
            suffix, kind, battle_eligible = supplemental_templates[body_index % len(supplemental_templates)]
            orbit_index = body_index + 1
            bodies.append({
                "id": f"{system['id']}_body_{orbit_index}",
                "systemId": system["id"],
                "parentBodyId": existing[0]["id"] if kind == "moon" and existing else None,
                "name": f"{system['name']} {suffix}",
                "kind": kind,
                "orbitIndex": orbit_index,
                "orbitAngle": (sum(ord(character) for character in system["id"]) + orbit_index * 97) % 360,
                "displayRadius": 4 + orbit_index % 3,
                "battleEligible": battle_eligible,
                "source": "homebrew",
            })

    partitions = _sector_partitions()
    sectors = []
    for sector_id, name in SECTOR_NAMES.items():
        label_x, label_y = SECTOR_SITES[sector_id]
        sectors.append({
            "id": sector_id,
            "name": name,
            "boundary": partitions[sector_id],
            "label": [round(label_x, 1), round(label_y, 1)],
        })

    grouped_subregions: dict[tuple[str, str], list[str]] = defaultdict(list)
    for system_id, region in original_regions.items():
        if region in {"contested", "quarantined", "black_reef"}:
            grouped_subregions[(sector_by_system[system_id], region)].append(system_id)
    subregion_names = {
        "contested": "Contested Zone",
        "quarantined": "Quarantined Worlds",
        "black_reef": "Black Reef",
    }
    subregions = [
        {
            "id": f"{region}_{sector_id}",
            "name": subregion_names[region],
            "sectorId": sector_id,
            "systemIds": sorted(system_ids),
            "status": region,
        }
        for (sector_id, region), system_ids in sorted(grouped_subregions.items())
    ]

    routes_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in legacy_edges:
        source_id = id_by_name[str(edge["source"])]
        target_id = id_by_name[str(edge["target"])]
        if source_id == target_id:
            continue
        pair = tuple(sorted((source_id, target_id)))
        same_sector = sector_by_system[source_id] == sector_by_system[target_id]
        proximity = str(edge.get("proximity") or "medium")
        route = {
            "id": f"{pair[0]}__{pair[1]}",
            "sourceSystemId": source_id,
            "targetSystemId": target_id,
            "routeType": "charted_warp",
            "baseTransitHours": _route_hours(float(edge.get("distance") or 100), same_sector, proximity),
            "risk": _route_risk(proximity, same_sector),
            "status": "open",
        }
        current = routes_by_pair.get(pair)
        if current is None or route["baseTransitHours"] < current["baseTransitHours"]:
            routes_by_pair[pair] = route

    routes = list(routes_by_pair.values())

    for target_name, hours, risk in (
        ("Tsua'Malor", 30, "moderate"),
        ("Skapula", 42, "high"),
        ("Sagacity", 54, "high"),
    ):
        target_id = id_by_name[target_name]
        pair = tuple(sorted((FORTRESS_SYSTEM_ID, target_id)))
        routes.append({
            "id": f"{pair[0]}__{pair[1]}",
            "sourceSystemId": FORTRESS_SYSTEM_ID,
            "targetSystemId": target_id,
            "routeType": "fortress_corridor",
            "baseTransitHours": hours,
            "risk": risk,
            "status": "open",
        })

    return validate_geography({
        "schemaVersion": 1,
        "chartBoundary": [[x, y] for x, y in CHART_BOUNDARY],
        "sectors": sectors,
        "subregions": subregions,
        "systems": systems,
        "bodies": bodies,
        "routes": routes,
        "landmarks": {
            "fortressBodyId": FORTRESS_BODY_ID,
            "eriochBodyId": ERIOCH_BODY_ID,
        },
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true", help="Write the validated geography document")
    args = parser.parse_args()

    graph = json.loads(args.source.read_text(encoding="utf-8"))
    geography = build_geography(graph)
    legacy_names = {str(node["id"]) for node in graph.get("nodes", [])}
    migrated_names = {system["name"] for system in geography["systems"] if system["id"] != FORTRESS_SYSTEM_ID}
    expected_system_names = (legacy_names - set(RECIDIOUS_PLANETS)) | {"Recidious"}
    if expected_system_names != migrated_names:
        raise ValueError("migration did not preserve every legacy system anchor")
    if not legacy_names <= {body["name"] for body in geography["bodies"]}:
        raise ValueError("migration did not preserve every legacy location as a body")
    if len(geography["sectors"]) != 7:
        raise ValueError("migration must produce exactly seven macro-sectors")

    fortress_system = next(body["systemId"] for body in geography["bodies"] if body["id"] == FORTRESS_BODY_ID)
    erioch_system = next(body["systemId"] for body in geography["bodies"] if body["id"] == ERIOCH_BODY_ID)
    route = shortest_route(geography, fortress_system, erioch_system)
    print(
        f"Validated {len(geography['sectors'])} sectors, {len(geography['systems'])} systems, "
        f"{len(geography['bodies'])} bodies, and {len(geography['routes'])} routes."
    )
    print(f"Fortress Jericho to Erioch: {route['baseTransitHours']:.1f} baseline hours across {len(route['routeIds'])} legs.")

    if not args.write:
        print("Dry run only. Re-run with --write to create the geography file.")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(geography, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()