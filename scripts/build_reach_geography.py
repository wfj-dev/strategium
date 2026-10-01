#!/usr/bin/env python3
"""Build the Strategium's hierarchical geography from the legacy Reach graph."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geography import shortest_route, validate_geography  # noqa: E402

DEFAULT_SOURCE = ROOT.parent / "discord-bots" / "op-scribe-servitor" / "reference" / "jericho_reach_graph.json"
DEFAULT_OUTPUT = ROOT / "data" / "reach_geography.json"

# The whole chart is the Jericho Reach; these are its canon regions (salients,
# warzones, and stellar phenomena), not separate Imperial sectors.
SECTOR_NAMES = {
    "hadex_anomaly": "Hadex Anomaly",
    "iron_collar": "Iron Collar",
    "orpheus_salient": "Orpheus Salient",
    "acheros_salient": "Acheros Salient",
    "cellebos_warzone": "Cellebos Warzone",
    "canis_salient": "Canis Salient",
    "greyhell_front": "Greyhell Front",
    "black_reef": "Black Reef",
    "coreward_marches": "Coreward Marches",
    "outer_reach": "Outer Reach",
}

MAP_X_SCALE = 1.75

# Region shapes are traced from this artwork. The frontend draws the same artwork
# over the world rectangle ART_ORIGIN + pixel * ART_SCALE (keep REACH_ART in sync).
SECTOR_EDGES_IMAGE = ROOT / "assets" / "Jericho_Warp_Storm_-_Sector_Edges.webp"
ART_ORIGIN = (-70.0, 10.0)
ART_SCALE = 1.34
SECTOR_INSET_PX = 9
SYSTEM_SPACING_PX = 15
# Mirrors the frontend bodyPosition(): orbit radius in world units and the flattened y axis.
ORBIT_BASE = 5.0
ORBIT_STEP = 4.0
MOON_ORBIT_BASE = 2.0
MOON_ORBIT_STEP = 1.1
ORBIT_ASPECT = 0.46
# Extra world-unit clearance between neighbouring systems' outermost orbits.
ORBIT_CLEARANCE = 4.0
MIN_SYSTEMS_PER_SECTOR = 3

# One artwork pixel inside each hand-drawn region, arranged like the canon Reach map.
# The map is charted by known warp-lanes, not real-space distance, so the drawn hub
# (the circle) is the Outer Reach -- the Watch Master's exile posting -- while Hadex
# Anomaly, the Reach's true (and uninhabitable) heart, sits in an outer wedge instead.
SECTOR_SEEDS = {
    "hadex_anomaly": (1214, 555),
    "iron_collar": (540, 163),
    "orpheus_salient": (1004, 261),
    "acheros_salient": (562, 381),
    "cellebos_warzone": (828, 389),
    "canis_salient": (349, 399),
    "greyhell_front": (526, 616),
    "black_reef": (646, 687),
    "coreward_marches": (915, 621),
    "outer_reach": (648, 583),
}

# Legacy graph regions -> Reach regions; named worlds follow their canon region.
LEGACY_REGIONS = {
    "hadex": "hadex_anomaly",
    "iron_collar": "iron_collar",
    "orpheus_salient": "orpheus_salient",
    "acheros_salient": "acheros_salient",
    "cellebos_warzone": "cellebos_warzone",
    "canis_salient": "canis_salient",
    "quarantined": "canis_salient",
    "black_reef": "black_reef",
    "slinnar_drift": "outer_reach",
    "contested": "coreward_marches",
}
WORLD_REGIONS = {
    **dict.fromkeys(("Bekrin", "Baraban", "Dakinor", "Veren", "Ravacene"), "greyhell_front"),
    **dict.fromkeys(("Zurcon", "Iphigenia"), "black_reef"),
    **dict.fromkeys(("Tabius Rasa", "Ries", "Iobel", "Melancholia", "Castiel", "Nunc", "Octavian"), "cellebos_warzone"),
    "Polyphemos": "outer_reach",
}

FORTRESS_SYSTEM_ID = "exul"
FORTRESS_BODY_ID = "watch_fortress_jericho"
ERIOCH_BODY_ID = "erioch"
RECIDIOUS_SYSTEM_ID = "recidious"
RECIDIOUS_PLANETS = ("Kadaku", "Avarax", "Demerium")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return slug[:80]


def _to_world(point: tuple[float, float]) -> tuple[float, float]:
    return (
        round(ART_ORIGIN[0] + point[0] * ART_SCALE, 3),
        round(ART_ORIGIN[1] + point[1] * ART_SCALE, 3),
    )


@lru_cache(maxsize=1)
def _traced_sectors() -> dict[str, Any]:
    """Split the hand-drawn sector artwork into ten labelled regions."""
    import cv2
    import numpy as np
    from PIL import Image

    alpha = np.array(Image.open(SECTOR_EDGES_IMAGE).convert("RGBA"))[:, :, 3]
    lines = cv2.dilate((alpha > 20).astype(np.uint8), np.ones((3, 3), np.uint8))
    _, components = cv2.connectedComponents((1 - lines).astype(np.uint8), connectivity=4)
    outside = len(SECTOR_SEEDS) + 1
    labels = np.zeros(components.shape, dtype=np.int32)
    labels[components == components[0, 0]] = outside
    used = {int(components[0, 0])}
    for index, (x, y) in enumerate(SECTOR_SEEDS.values(), start=1):
        component = int(components[y, x])
        if component == 0 or component in used:
            raise ValueError(f"sector seed {(x, y)} does not identify a distinct region")
        used.add(component)
        labels[components == component] = index

    # Line pixels join whichever region (or the outside) is nearest, so regions tile.
    known = labels != 0
    _, nearest = cv2.distanceTransformWithLabels((~known).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    lookup = np.zeros(int(nearest.max()) + 1, dtype=np.int32)
    lookup[nearest[known]] = labels[known]
    labels = np.where(known, labels, lookup[nearest])

    def outline(mask: Any) -> list[tuple[float, float]]:
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        contour = max(contours, key=cv2.contourArea)
        return [(float(x) + 0.5, float(y) + 0.5) for x, y in cv2.approxPolyDP(contour, 1.0, True)[:, 0, :]]

    sectors: dict[str, dict[str, Any]] = {}
    interior = np.zeros(labels.shape, dtype=np.int32)
    for index, sector_id in enumerate(SECTOR_SEEDS, start=1):
        mask = labels == index
        depth = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
        y, x = np.unravel_index(int(depth.argmax()), depth.shape)
        interior[depth >= SECTOR_INSET_PX] = index
        sectors[sector_id] = {
            "outline": outline(mask),
            "label": (float(x) + 0.5, float(y) + 0.5),
            "depth": float(depth.max()),
        }
    return {
        "sectors": sectors,
        "boundary": outline((labels > 0) & (labels < outside)),
        "interior": interior,
    }


def _hub_geometry() -> tuple[tuple[float, float], float]:
    """Geometry of the drawn hub circle (charted as the Outer Reach)."""
    core = _traced_sectors()["sectors"]["outer_reach"]
    return _to_world(core["label"]), core["depth"] * ART_SCALE


def _orbit_extents(bodies: list[dict[str, Any]]) -> dict[str, float]:
    """World-unit x radius of each system's outermost orbit, moons included."""
    by_id = {body["id"]: body for body in bodies}

    def reach(body: dict[str, Any]) -> float:
        if body["kind"] == "star":
            return 0.0
        parent = by_id.get(body["parentBodyId"] or "")
        if parent:
            return reach(parent) + MOON_ORBIT_BASE + body["orbitIndex"] * MOON_ORBIT_STEP
        return ORBIT_BASE + body["orbitIndex"] * ORBIT_STEP

    extents: dict[str, float] = defaultdict(float)
    for body in bodies:
        extents[body["systemId"]] = max(extents[body["systemId"]], reach(body))
    return extents


def _place_systems(
    systems: list[dict[str, Any]], regions: dict[str, str], extents: dict[str, float]
) -> set[str]:
    """Place each system in its canon region, spread like the legacy chart and off the drawn lines.

    Returns the ids of homebrew filler systems dropped because their orbits would overlap another's.
    """
    import numpy as np

    traced = _traced_sectors()
    interior = traced["interior"]
    sector_ids = list(SECTOR_SEEDS)
    ys, xs = np.nonzero(interior)
    pixel_sector = interior[ys, xs]
    available = np.ones(len(xs), dtype=bool)
    spacing = SYSTEM_SPACING_PX ** 2

    pixel: dict[str, tuple[float, float]] = {}

    def claim(point: tuple[float, float], system: dict[str, Any], sector_index: int) -> None:
        available[(xs - point[0]) ** 2 + (ys - point[1]) ** 2 < spacing] = False
        pixel[system["id"]] = point
        system["x"], system["y"] = _to_world(point)
        system["sectorId"] = sector_ids[sector_index - 1]

    def nearest_in(sector_index: int, point: tuple[float, float]) -> tuple[float, float]:
        candidates = np.nonzero(available & (pixel_sector == sector_index))[0]
        if not len(candidates):
            raise ValueError(f"region {sector_ids[sector_index - 1]} has no room left")
        best = candidates[int(np.argmin((xs[candidates] - point[0]) ** 2 + (ys[candidates] - point[1]) ** 2))]
        return float(xs[best]), float(ys[best])

    by_id = {system["id"]: system for system in systems}
    # The Watch Master's fortress sits at the hub -- the Outer Reach is charted by
    # warp-lane, not real-space, proximity, so the Reach's edge is drawn centrally.
    # Erioch is ancient and veiled, kept near the Anomaly that swallowed the old sector capital.
    outer_index = sector_ids.index("outer_reach") + 1
    claim(traced["sectors"]["outer_reach"]["label"], by_id[FORTRESS_SYSTEM_ID], outer_index)
    hadex_index = sector_ids.index("hadex_anomaly") + 1
    claim(traced["sectors"]["hadex_anomaly"]["label"], by_id[ERIOCH_BODY_ID], hadex_index)

    movable = [system for system in systems if system["id"] not in {FORTRESS_SYSTEM_ID, ERIOCH_BODY_ID}]
    assigned = {system["id"]: sector_ids.index(regions[system["id"]]) + 1 for system in movable}
    legacy = {system["id"]: (system["x"], system["y"]) for system in movable}
    counts = {index: 0 for index in range(1, len(sector_ids) + 1)}
    for index in assigned.values():
        counts[index] += 1
    counts[outer_index] += 1
    counts[hadex_index] += 1
    for index in counts:
        label = traced["sectors"][sector_ids[index - 1]]["label"]
        while counts[index] < MIN_SYSTEMS_PER_SECTOR:
            donors = [system_id for system_id, source in assigned.items() if counts[source] > MIN_SYSTEMS_PER_SECTOR + 1]
            donor = min(donors, key=lambda system_id: (math.dist(_to_world(label), legacy[system_id]), system_id))
            counts[assigned[donor]] -= 1
            assigned[donor] = index
            counts[index] += 1

    for index in counts:
        members = [system for system in movable if assigned[system["id"]] == index]
        if not members:
            continue
        pixels = pixel_sector == index
        box_x = (float(xs[pixels].min()), float(xs[pixels].max()))
        box_y = (float(ys[pixels].min()), float(ys[pixels].max()))
        legacy_x = [legacy[system["id"]][0] for system in members]
        legacy_y = [legacy[system["id"]][1] for system in members]
        for system in members:
            x, y = legacy[system["id"]]
            rng = random.Random(f"scatter:{system['id']}")
            # Jitter well beyond the remap grid so the result reads as natural clumps and gaps, not a lattice.
            target_x = (
                box_x[0] + (x - min(legacy_x)) / ((max(legacy_x) - min(legacy_x)) or 1.0) * (box_x[1] - box_x[0])
                + rng.uniform(-1, 1) * (box_x[1] - box_x[0]) * .3
            )
            target_y = (
                box_y[0] + (y - min(legacy_y)) / ((max(legacy_y) - min(legacy_y)) or 1.0) * (box_y[1] - box_y[0])
                + rng.uniform(-1, 1) * (box_y[1] - box_y[0]) * .3
            )
            # The bounding box is rectangular but these regions are irregular polygons: a remapped
            # target that lands outside the true shape always snaps to the nearest edge pixel,
            # which clusters systems along borders. Fall back to a genuinely random interior pixel.
            px, py = int(round(target_x)), int(round(target_y))
            inside = 0 <= py < interior.shape[0] and 0 <= px < interior.shape[1] and interior[py, px] == index
            if inside:
                target = (target_x, target_y)
            else:
                candidates = np.nonzero(available & (pixel_sector == index))[0]
                if not len(candidates):
                    raise ValueError(f"region {sector_ids[index - 1]} has no room left")
                choice = candidates[rng.randrange(len(candidates))]
                target = (float(xs[choice]), float(ys[choice]))
            claim(nearest_in(index, target), system, index)

    # No system's orbits may overlap another's. Orbits are ellipses flattened by ORBIT_ASPECT, so
    # stretching y by 1/ORBIT_ASPECT turns the test into plain circle separation (in art pixels).
    radius = {system["id"]: (extents[system["id"]] + ORBIT_CLEARANCE / 2) / ART_SCALE for system in systems}

    def overlaps(a: str, point: tuple[float, float], b: str) -> bool:
        other = pixel[b]
        return math.hypot(point[0] - other[0], (point[1] - other[1]) / ORBIT_ASPECT) < radius[a] + radius[b]

    # Charted systems are never dropped; nudge each one that overlaps an already-settled one.
    pinned = [by_id[FORTRESS_SYSTEM_ID], by_id[ERIOCH_BODY_ID]]
    charted = [system for system in movable if not {"periphery", "expanse"} & set(system["tags"])]
    settled = list(pinned)
    for system in charted:
        if any(overlaps(system["id"], pixel[system["id"]], other["id"]) for other in settled):
            available[:] = True
            for other in settled:
                x, y = pixel[other["id"]]
                reach = radius[system["id"]] + radius[other["id"]]
                available[(xs - x) ** 2 + ((ys - y) / ORBIT_ASPECT) ** 2 < reach ** 2] = False
            index = sector_ids.index(system["sectorId"]) + 1
            claim(nearest_in(index, pixel[system["id"]]), system, index)
        settled.append(system)

    # Then repeatedly drop the filler system whose orbits overlap the most others.
    points = np.array([pixel[system["id"]] for system in systems])
    radii = np.array([radius[system["id"]] for system in systems])
    dx = points[:, None, 0] - points[None, :, 0]
    dy = (points[:, None, 1] - points[None, :, 1]) / ORBIT_ASPECT
    crowded = np.hypot(dx, dy) < radii[:, None] + radii[None, :]
    np.fill_diagonal(crowded, False)
    filler = np.array([bool({"periphery", "expanse"} & set(system["tags"])) for system in systems])
    alive = np.ones(len(systems), dtype=bool)
    while True:
        clashes = (crowded & alive[None, :]).sum(axis=1) * (alive & filler)
        if not clashes.any():
            break
        alive[int(np.argmax(clashes))] = False
    return {system["id"] for system, keep in zip(systems, alive) if not keep}


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
    for index, node in enumerate(legacy_nodes):
        name = str(node["id"])
        if name in RECIDIOUS_PLANETS:
            continue
        system_id = id_by_name[name]
        original_regions[system_id] = str(node.get("region") or "")
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
            "sectorId": "hadex_anomaly",
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
            "name": f"Watch Fortress {name}" if node.get("type") == "watch_station" else name,
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
        "sectorId": "hadex_anomaly",
        "x": recidious_x,
        "y": recidious_y,
        "classification": "charted_system",
        "primaryBodyId": "recidious_star",
        "source": "canon",
        "tags": ["game_planet", "multi_body_system"],
    })
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
        "name": "Exul",
        "sectorId": "hadex_anomaly",
        "x": 0.0,
        "y": 0.0,
        "classification": "watch_fortress_system",
        "primaryBodyId": "exul_star",
        "source": "homebrew",
        "tags": ["fortress", "logistics_hub"],
    })
    bodies.extend([
        {
            "id": "exul_star",
            "systemId": FORTRESS_SYSTEM_ID,
            "parentBodyId": None,
            "name": "Exul Primary",
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

    # Double the charted systems, then add a further ~50% with a second homebrew sibling.
    for system in list(systems):
        if system["id"] in (FORTRESS_SYSTEM_ID, ERIOCH_BODY_ID, RECIDIOUS_SYSTEM_ID):
            continue
        for suffix_id, suffix_name in (("periphery", "Periphery"), ("outpost", "Outpost")):
            sibling_id = f"{system['id']}_{suffix_id}"
            rng = random.Random(sibling_id)
            original_regions[sibling_id] = original_regions[system["id"]]
            systems.append({
                "id": sibling_id,
                "name": f"{system['name']} {suffix_name}",
                "sectorId": system["sectorId"],
                "x": system["x"] + rng.uniform(-40, 40),
                "y": system["y"] + rng.uniform(-40, 40),
                "classification": "charted_system",
                "primaryBodyId": f"{sibling_id}_star",
                "source": "homebrew",
                "tags": ["periphery"],
            })
            bodies.append({
                "id": f"{sibling_id}_star",
                "systemId": sibling_id,
                "parentBodyId": None,
                "name": f"{system['name']} {suffix_name} Primary",
                "kind": "star",
                "orbitIndex": 0,
                "orbitAngle": 0,
                "displayRadius": 5,
                "battleEligible": False,
                "source": "homebrew",
            })


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

    # Rebalance: some canon regions are drawn far larger than others, so pad sparse ones toward
    # an area-proportional share instead of leaving them empty relative to crowded neighbours.
    regions = {
        system["id"]: WORLD_REGIONS.get(system["name"]) or LEGACY_REGIONS[original_regions[system["id"]]]
        for system in systems if system["id"] in original_regions
    }
    interior = _traced_sectors()["interior"]
    sector_ids = list(SECTOR_SEEDS)
    areas = {sector_id: int((interior == index + 1).sum()) for index, sector_id in enumerate(sector_ids)}
    # Square-root the area so tiny regions (e.g. Black Reef) aren't left near-empty next to huge ones.
    weights = {sector_id: math.sqrt(area) for sector_id, area in areas.items()}
    total_weight = sum(weights.values())
    counts = {sector_id: 0 for sector_id in sector_ids}
    for region in regions.values():
        counts[region] += 1
    counts["outer_reach"] += 1  # Exul
    counts["hadex_anomaly"] += 1  # Erioch
    total_pool = len(systems)
    by_id = {system["id"]: system for system in systems}
    global_x = sum(system["x"] for system in systems) / len(systems)
    global_y = sum(system["y"] for system in systems) / len(systems)
    for sector_id in sector_ids:
        target_count = round(total_pool * weights[sector_id] / total_weight)
        members = [system_id for system_id, region in regions.items() if region == sector_id]
        for pad_index in range(max(0, target_count - counts[sector_id])):
            pad_id = f"{sector_id}_expanse_{pad_index}"
            rng = random.Random(pad_id)
            basis_id = rng.choice(members) if members else None
            basis = by_id[basis_id] if basis_id else None
            basis_x, basis_y = (basis["x"], basis["y"]) if basis else (global_x, global_y)
            name = f"{basis['name']} Expanse" if basis else f"{SECTOR_NAMES[sector_id]} Expanse {pad_index}"
            systems.append({
                "id": pad_id,
                "name": name,
                "sectorId": sector_id,
                "x": basis_x + rng.uniform(-70, 70),
                "y": basis_y + rng.uniform(-70, 70),
                "classification": "charted_system",
                "primaryBodyId": f"{pad_id}_star",
                "source": "homebrew",
                "tags": ["expanse"],
            })
            regions[pad_id] = sector_id
            bodies.append({
                "id": f"{pad_id}_star",
                "systemId": pad_id,
                "parentBodyId": None,
                "name": f"{name} Primary",
                "kind": "star",
                "orbitIndex": 0,
                "orbitAngle": 0,
                "displayRadius": 5,
                "battleEligible": False,
                "source": "homebrew",
            })
            target_count_bodies = 3 + sum(ord(character) for character in pad_id) % 3
            for body_index in range(target_count_bodies):
                suffix, kind, battle_eligible = supplemental_templates[body_index % len(supplemental_templates)]
                orbit_index = body_index + 1
                bodies.append({
                    "id": f"{pad_id}_body_{orbit_index}",
                    "systemId": pad_id,
                    "parentBodyId": None,
                    "name": f"{name} {suffix}",
                    "kind": kind,
                    "orbitIndex": orbit_index,
                    "orbitAngle": (sum(ord(character) for character in pad_id) + orbit_index * 97) % 360,
                    "displayRadius": 4 + orbit_index % 3,
                    "battleEligible": battle_eligible,
                    "source": "homebrew",
                })

    removed = _place_systems(systems, regions, _orbit_extents(bodies))
    systems = [system for system in systems if system["id"] not in removed]
    bodies = [body for body in bodies if body["systemId"] not in removed]
    for system_id in removed:
        original_regions.pop(system_id, None)
    sector_by_system = {system["id"]: system["sectorId"] for system in systems}
    traced = _traced_sectors()
    sectors = []
    for sector_id, name in SECTOR_NAMES.items():
        shape = traced["sectors"][sector_id]
        sectors.append({
            "id": sector_id,
            "name": name,
            "boundary": [list(_to_world(point)) for point in shape["outline"]],
            "label": list(_to_world(shape["label"])),
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
    by_id = {system["id"]: system for system in systems}
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

    for system in systems:
        if "periphery" not in system["tags"]:
            continue
        parent_id = system["id"].removesuffix("_periphery").removesuffix("_outpost")
        pair = tuple(sorted((parent_id, system["id"])))
        routes_by_pair.setdefault(pair, {
            "id": f"{pair[0]}__{pair[1]}",
            "sourceSystemId": pair[0],
            "targetSystemId": pair[1],
            "routeType": "charted_warp",
            "baseTransitHours": 8.0,
            "risk": "low",
            "status": "open",
        })

    for system in systems:
        if "expanse" not in system["tags"]:
            continue
        nearest_id = min(
            (other["id"] for other in systems if other["id"] != system["id"]),
            key=lambda other_id: math.dist((system["x"], system["y"]), (by_id[other_id]["x"], by_id[other_id]["y"])),
        )
        pair = tuple(sorted((nearest_id, system["id"])))
        routes_by_pair.setdefault(pair, {
            "id": f"{pair[0]}__{pair[1]}",
            "sourceSystemId": pair[0],
            "targetSystemId": pair[1],
            "routeType": "charted_warp",
            "baseTransitHours": 10.0,
            "risk": "moderate",
            "status": "open",
        })

    # Guarantee every system can reach every other: union disconnected clusters with their
    # single nearest cross-cluster pair, same as a minimum-spanning-tree completion pass.
    parent = {system["id"]: system["id"] for system in systems}

    def find(system_id: str) -> str:
        while parent[system_id] != system_id:
            parent[system_id] = parent[parent[system_id]]
            system_id = parent[system_id]
        return system_id

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for route in routes_by_pair.values():
        union(route["sourceSystemId"], route["targetSystemId"])

    clusters: dict[str, list[str]] = defaultdict(list)
    for system in systems:
        clusters[find(system["id"])].append(system["id"])
    cluster_list = list(clusters.values())
    while len(cluster_list) > 1:
        base = cluster_list[0]
        best = None
        for other in cluster_list[1:]:
            for a in base:
                for b in other:
                    distance = math.dist((by_id[a]["x"], by_id[a]["y"]), (by_id[b]["x"], by_id[b]["y"]))
                    if best is None or distance < best[0]:
                        best = (distance, a, b, other)
        _, a, b, other = best
        pair = tuple(sorted((a, b)))
        routes_by_pair.setdefault(pair, {
            "id": f"{pair[0]}__{pair[1]}",
            "sourceSystemId": pair[0],
            "targetSystemId": pair[1],
            "routeType": "charted_warp",
            "baseTransitHours": 16.0,
            "risk": "high",
            "status": "open",
        })
        union(a, b)
        base.extend(other)
        cluster_list.remove(other)

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
        "chartBoundary": [list(_to_world(point)) for point in traced["boundary"]],
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
    migrated_names = {
        system["name"] for system in geography["systems"]
        if system["id"] != FORTRESS_SYSTEM_ID and not ({"periphery", "expanse"} & set(system["tags"]))
    }
    expected_system_names = (legacy_names - set(RECIDIOUS_PLANETS)) | {"Recidious"}
    if expected_system_names != migrated_names:
        raise ValueError("migration did not preserve every legacy system anchor")
    if not legacy_names <= {body["name"].removeprefix("Watch Fortress ") for body in geography["bodies"]}:
        raise ValueError("migration did not preserve every legacy location as a body")
    if len(geography["sectors"]) != 10:
        raise ValueError("migration must produce exactly ten map sectors")

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