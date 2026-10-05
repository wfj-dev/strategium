"""Validated hierarchical geography and route planning for the Jericho Reach."""

from __future__ import annotations

import heapq
import json
import math
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_SECTORS = 40
MAX_SUBREGIONS = 100
MAX_SYSTEMS = 500
MAX_BODIES = 2500
MAX_ROUTES = 3000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,79}$")
_SOURCES = {"canon", "local_reference", "homebrew"}
_ROUTE_STATUSES = {"open", "restricted", "closed"}
_RISKS = {"low", "moderate", "high", "extreme"}


def _objects(value: Any, limit: int, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    if len(value) > limit:
        raise ValueError(f"{label} exceeds the limit of {limit}")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} entries must be objects")
    return value


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase identifier")
    return value


def _text(value: Any, label: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()[:limit]


def _number(value: Any, label: str, *, minimum: float = -10000, maximum: float = 10000) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return number


def _point(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} must be an [x, y] point")
    return [_number(value[0], f"{label}.x"), _number(value[1], f"{label}.y")]


def _unique_id(item: dict[str, Any], seen: set[str], label: str) -> str:
    item_id = _id(item.get("id"), f"{label}.id")
    if item_id in seen:
        raise ValueError(f"duplicate {label} id: {item_id}")
    seen.add(item_id)
    return item_id


def _tags(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError(f"{label} must be an array of at most 20 identifiers")
    return [_id(tag, label) for tag in value]


def validate_geography(payload: Any) -> dict[str, Any]:
    """Validate and sanitize a hierarchical Reach geography document."""
    if not isinstance(payload, dict):
        raise TypeError("geography must be an object")
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"schemaVersion must be {SCHEMA_VERSION}")

    chart_boundary = [_point(point, "chartBoundary") for point in payload.get("chartBoundary", [])]
    if len(chart_boundary) < 3:
        raise ValueError("chartBoundary must contain at least three points")

    sector_ids: set[str] = set()
    sector_numbers: set[int] = set()
    sectors = []
    for raw in _objects(payload.get("sectors"), MAX_SECTORS, "sectors"):
        sector_id = _unique_id(raw, sector_ids, "sector")
        boundary = [_point(point, f"sector {sector_id} boundary") for point in raw.get("boundary", [])]
        if len(boundary) < 3:
            raise ValueError(f"sector {sector_id} boundary must contain at least three points")
        status = raw.get("status", "secure")
        if status not in {"secure", "critical", "lost"}:
            raise ValueError(f"sector {sector_id} has invalid status")
        sectors.append({
            "id": sector_id,
            "name": _text(raw.get("name"), f"sector {sector_id} name"),
            "boundary": boundary,
            "label": _point(raw.get("label"), f"sector {sector_id} label"),
            "status": status,
        })
        if "number" in raw:
            number = raw["number"]
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 29 or number in sector_numbers:
                raise ValueError(f"sector {sector_id} has invalid or duplicate number")
            sector_numbers.add(number)
            sectors[-1]["number"] = number

    system_ids: set[str] = set()
    systems = []
    for raw in _objects(payload.get("systems"), MAX_SYSTEMS, "systems"):
        system_id = _unique_id(raw, system_ids, "system")
        sector_id = _id(raw.get("sectorId"), f"system {system_id} sectorId")
        if sector_id not in sector_ids:
            raise ValueError(f"system {system_id} references unknown sector {sector_id}")
        source = _text(raw.get("source"), f"system {system_id} source", 30)
        if source not in _SOURCES:
            raise ValueError(f"system {system_id} has invalid source")
        systems.append({
            "id": system_id,
            "name": _text(raw.get("name"), f"system {system_id} name"),
            "sectorId": sector_id,
            "x": _number(raw.get("x"), f"system {system_id} x"),
            "y": _number(raw.get("y"), f"system {system_id} y"),
            "classification": _id(raw.get("classification"), f"system {system_id} classification"),
            "primaryBodyId": _id(raw.get("primaryBodyId"), f"system {system_id} primaryBodyId"),
            "source": source,
            "tags": _tags(raw.get("tags"), f"system {system_id} tags"),
        })

    body_ids: set[str] = set()
    bodies = []
    body_system: dict[str, str] = {}
    for raw in _objects(payload.get("bodies"), MAX_BODIES, "bodies"):
        body_id = _unique_id(raw, body_ids, "body")
        system_id = _id(raw.get("systemId"), f"body {body_id} systemId")
        if system_id not in system_ids:
            raise ValueError(f"body {body_id} references unknown system {system_id}")
        parent_id = raw.get("parentBodyId")
        if parent_id is not None:
            parent_id = _id(parent_id, f"body {body_id} parentBodyId")
        source = _text(raw.get("source"), f"body {body_id} source", 30)
        if source not in _SOURCES:
            raise ValueError(f"body {body_id} has invalid source")
        orbit_index = int(_number(raw.get("orbitIndex", 0), f"body {body_id} orbitIndex", minimum=0, maximum=100))
        bodies.append({
            "id": body_id,
            "systemId": system_id,
            "parentBodyId": parent_id,
            "name": _text(raw.get("name"), f"body {body_id} name"),
            "kind": _id(raw.get("kind"), f"body {body_id} kind"),
            "orbitIndex": orbit_index,
            "orbitAngle": _number(raw.get("orbitAngle", 0), f"body {body_id} orbitAngle", minimum=0, maximum=360),
            "displayRadius": _number(raw.get("displayRadius", 4), f"body {body_id} displayRadius", minimum=1, maximum=30),
            "battleEligible": raw.get("battleEligible") is True,
            "source": source,
        })
        body_system[body_id] = system_id
        if "stellarClass" in raw:
            stellar_class = raw["stellarClass"]
            if raw.get("kind") != "star" or not isinstance(stellar_class, str) or stellar_class not in {"O", "B", "A", "F", "G", "K", "M"}:
                raise ValueError(f"body {body_id} has invalid stellarClass")
            bodies[-1]["stellarClass"] = stellar_class

    for body in bodies:
        parent_id = body["parentBodyId"]
        if parent_id is not None:
            if parent_id not in body_ids:
                raise ValueError(f"body {body['id']} references unknown parent body {parent_id}")
            if body_system[parent_id] != body["systemId"]:
                raise ValueError(f"body {body['id']} parent belongs to another system")

    parents = {body["id"]: body["parentBodyId"] for body in bodies}
    for body in bodies:
        ancestor = body["id"]
        visited = set()
        while ancestor is not None:
            if ancestor in visited:
                raise ValueError(f"body {body['id']} has a parent cycle")
            visited.add(ancestor)
            ancestor = parents[ancestor]

    for system in systems:
        primary_id = system["primaryBodyId"]
        if primary_id not in body_ids:
            raise ValueError(f"system {system['id']} references unknown primary body {primary_id}")
        if body_system[primary_id] != system["id"]:
            raise ValueError(f"system {system['id']} primary body belongs to another system")

    subregion_ids: set[str] = set()
    subregions = []
    for raw in _objects(payload.get("subregions", []), MAX_SUBREGIONS, "subregions"):
        subregion_id = _unique_id(raw, subregion_ids, "subregion")
        sector_id = _id(raw.get("sectorId"), f"subregion {subregion_id} sectorId")
        if sector_id not in sector_ids:
            raise ValueError(f"subregion {subregion_id} references unknown sector {sector_id}")
        member_systems = [_id(value, f"subregion {subregion_id} systemIds") for value in raw.get("systemIds", [])]
        if any(system_id not in system_ids for system_id in member_systems):
            raise ValueError(f"subregion {subregion_id} references unknown system")
        subregions.append({
            "id": subregion_id,
            "name": _text(raw.get("name"), f"subregion {subregion_id} name"),
            "sectorId": sector_id,
            "systemIds": list(dict.fromkeys(member_systems)),
            "status": _id(raw.get("status", "charted"), f"subregion {subregion_id} status"),
        })

    route_ids: set[str] = set()
    route_pairs: set[tuple[str, str]] = set()
    routes = []
    for raw in _objects(payload.get("routes"), MAX_ROUTES, "routes"):
        route_id = _unique_id(raw, route_ids, "route")
        source_id = _id(raw.get("sourceSystemId"), f"route {route_id} sourceSystemId")
        target_id = _id(raw.get("targetSystemId"), f"route {route_id} targetSystemId")
        if source_id not in system_ids or target_id not in system_ids:
            raise ValueError(f"route {route_id} references unknown system")
        if source_id == target_id:
            raise ValueError(f"route {route_id} cannot connect a system to itself")
        pair = tuple(sorted((source_id, target_id)))
        if pair in route_pairs:
            raise ValueError(f"duplicate route between {pair[0]} and {pair[1]}")
        route_pairs.add(pair)
        status = _text(raw.get("status"), f"route {route_id} status", 20)
        risk = _text(raw.get("risk"), f"route {route_id} risk", 20)
        if status not in _ROUTE_STATUSES:
            raise ValueError(f"route {route_id} has invalid status")
        if risk not in _RISKS:
            raise ValueError(f"route {route_id} has invalid risk")
        routes.append({
            "id": route_id,
            "sourceSystemId": source_id,
            "targetSystemId": target_id,
            "routeType": _id(raw.get("routeType"), f"route {route_id} routeType"),
            "baseTransitHours": _number(raw.get("baseTransitHours"), f"route {route_id} baseTransitHours", minimum=1, maximum=24 * 60),
            "risk": risk,
            "status": status,
        })

    landmarks = payload.get("landmarks")
    if not isinstance(landmarks, dict):
        raise TypeError("landmarks must be an object")
    fortress_body_id = _id(landmarks.get("fortressBodyId"), "landmarks.fortressBodyId")
    erioch_body_id = _id(landmarks.get("eriochBodyId"), "landmarks.eriochBodyId")
    if fortress_body_id not in body_ids or erioch_body_id not in body_ids:
        raise ValueError("landmarks reference unknown body")
    if fortress_body_id == erioch_body_id:
        raise ValueError("Fortress Jericho and Erioch must be distinct landmarks")

    result = {
        "schemaVersion": SCHEMA_VERSION,
        "chartBoundary": chart_boundary,
        "sectors": sectors,
        "subregions": subregions,
        "systems": systems,
        "bodies": bodies,
        "routes": routes,
        "landmarks": {
            "fortressBodyId": fortress_body_id,
            "eriochBodyId": erioch_body_id,
        },
    }
    if "mapArt" in payload:
        art = payload["mapArt"]
        if not isinstance(art, dict):
            raise ValueError("mapArt must be an object")
        result["mapArt"] = {
            "x": _number(art.get("x"), "mapArt.x"),
            "y": _number(art.get("y"), "mapArt.y"),
            "scale": _number(art.get("scale"), "mapArt.scale", minimum=.01, maximum=10),
            "width": _number(art.get("width"), "mapArt.width", minimum=1, maximum=4096),
            "height": _number(art.get("height"), "mapArt.height", minimum=1, maximum=4096),
        }
    return result


def load_geography(path: Path) -> dict[str, Any]:
    """Load and validate a geography document from disk."""
    return validate_geography(json.loads(path.read_text(encoding="utf-8")))


def shortest_route(geography: dict[str, Any], origin_system_id: str, destination_system_id: str) -> dict[str, Any]:
    """Return the lowest-baseline-hours route over currently open links."""
    system_ids = {system["id"] for system in geography["systems"]}
    if origin_system_id not in system_ids or destination_system_id not in system_ids:
        raise ValueError("Route endpoint is not a known system")
    if origin_system_id == destination_system_id:
        return {"systemIds": [origin_system_id], "routeIds": [], "baseTransitHours": 0.0}

    adjacency: dict[str, list[tuple[str, float, str]]] = {system_id: [] for system_id in system_ids}
    for route in geography["routes"]:
        if route["status"] != "open":
            continue
        hours = float(route["baseTransitHours"])
        source_id = route["sourceSystemId"]
        target_id = route["targetSystemId"]
        adjacency[source_id].append((target_id, hours, route["id"]))
        adjacency[target_id].append((source_id, hours, route["id"]))

    queue: list[tuple[float, str]] = [(0.0, origin_system_id)]
    best = {origin_system_id: 0.0}
    previous: dict[str, tuple[str, str]] = {}
    while queue:
        total_hours, system_id = heapq.heappop(queue)
        if total_hours != best.get(system_id):
            continue
        if system_id == destination_system_id:
            break
        for next_id, route_hours, route_id in adjacency[system_id]:
            next_hours = total_hours + route_hours
            if next_hours < best.get(next_id, math.inf):
                best[next_id] = next_hours
                previous[next_id] = (system_id, route_id)
                heapq.heappush(queue, (next_hours, next_id))

    if destination_system_id not in best:
        raise ValueError(f"No open route from {origin_system_id} to {destination_system_id}")

    systems = [destination_system_id]
    routes = []
    current = destination_system_id
    while current != origin_system_id:
        current, route_id = previous[current]
        systems.append(current)
        routes.append(route_id)
    systems.reverse()
    routes.reverse()
    return {
        "systemIds": systems,
        "routeIds": routes,
        "baseTransitHours": best[destination_system_id],
    }