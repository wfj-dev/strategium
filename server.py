"""Strategium backend for bot snapshots, roster reads, and self-owned backstories."""

from __future__ import annotations

import base64
import email.utils
import hashlib
import hmac
import http.cookies
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from geography import load_geography

ROOT = Path(__file__).resolve().parent
CHAPTERS_REFERENCE_PATH = Path(
    os.getenv(
        "STRATEGIUM_CHAPTERS_REFERENCE_PATH",
        str(ROOT.parent / "discord-bots" / "op-scribe-servitor" / "reference" / "chapters.json"),
    )
)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            if val and not os.environ.get(key):
                os.environ[key] = val
    except Exception:
        pass


_load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
ROSTER_PATH = DATA_DIR / "roster_snapshot.json"
BACKSTORIES_PATH = DATA_DIR / "backstories.json"
REACH_PATH = DATA_DIR / "reach_snapshot.json"
GEOGRAPHY_PATH = DATA_DIR / "reach_geography.json"
RIBBONS_DIR = ROOT / "assets" / "ribbons"
RIBBON_URL_PREFIX = "/assets/ribbons/"
PAULDRONS_DIR = ROOT / "assets" / "Painted Pauldrons" / "Completed"
WEB_ASSETS_DIR = ROOT / "assets" / "web"
PAULDRON_THUMBS_DIR = WEB_ASSETS_DIR / "pauldrons"
PAULDRON_URL_PREFIX = "/assets/pauldrons/"
RANKS_DIR = WEB_ASSETS_DIR / "ranks"
RANK_URL_PREFIX = "/assets/ranks/"
RANK_IMAGES = {path.name: path for path in RANKS_DIR.glob("*.webp")} if RANKS_DIR.is_dir() else {}
RANK_CARDS_DIR = WEB_ASSETS_DIR / "rank-cards"
RANK_CARD_URL_PREFIX = "/assets/rank-cards/"
RANK_CARD_IMAGES = {path.name: path for path in RANK_CARDS_DIR.glob("*.webp")} if RANK_CARDS_DIR.is_dir() else {}
AMBIENCE_PATH = ROOT / "assets" / "ambience" / "fortress-ambience.mp3"
AMBIENCE_URL = "/assets/ambience/fortress-ambience.mp3"
RECORD_WALL_PATH = ROOT / "assets" / "record of blood wall.png"
RECORD_WALL_URL = "/assets/record-of-blood-wall.png"
JERICHO_SYMBOL_PATH = ROOT / "assets" / "jericho symbol.png"
JERICHO_SYMBOL_URL = "/assets/jericho-symbol.png"
JERICHO_SYMBOL_WEBP_PATH = WEB_ASSETS_DIR / "jericho-symbol.webp"
JERICHO_SYMBOL_WEBP_URL = "/assets/jericho-symbol.webp"
INQUISITORIAL_ROSETTE_PATH = ROOT / "assets" / "Inquisitorial_Rosette.png"
INQUISITORIAL_ROSETTE_URL = "/assets/inquisitorial-rosette.png"
INQUISITORIAL_ROSETTE_WEBP_PATH = WEB_ASSETS_DIR / "inquisitorial-rosette.webp"
INQUISITORIAL_ROSETTE_WEBP_URL = "/assets/inquisitorial-rosette.webp"
DISCORD_MARK_PATH = ROOT / "assets" / "discord-mark.svg"
DISCORD_MARK_URL = "/assets/discord-mark.svg"
FORTRESS_MAP_PATH = ROOT / "assets" / "Watch_Fortress_Jericho_Map.png"
FORTRESS_MAP_URL = "/assets/watch-fortress-jericho-map.png"
FORTRESS_MAP_WEBP_PATH = ROOT / "assets" / "Watch_Fortress_Jericho_Map.webp"
FORTRESS_MAP_WEBP_URL = "/assets/watch-fortress-jericho-map.webp"
REACH_BACKGROUND_WEBP_PATH = ROOT / "assets" / "Jericho_Warp_Storm.webp"
REACH_BACKGROUND_WEBP_URL = "/assets/reach-warp-storm.webp"
REACH_STARFIELD_WEBP_PATH = ROOT / "assets" / "Quiet_Stars.webp"
REACH_STARFIELD_WEBP_URL = "/assets/reach-starfield.webp"
REACH_SECTOR_EDGES_PATH = ROOT / "assets" / "Jericho_Warp_Storm_-_Sector_Edges.webp"
REACH_SECTOR_EDGES_URL = "/assets/reach-sector-edges.webp"
FORTRESS_HOVER_MASK_PATH = ROOT / "assets" / "atlas-hover-mask.png"
FORTRESS_HOVER_MASK_URL = "/assets/atlas-hover-mask.png"
FORTRESS_LAYERS_DIR = ROOT / "assets" / "Jericho Fortress Layers"
FORTRESS_LAYER_URL_PREFIX = "/assets/fortress-layers/"
FORTRESS_LAYER_WEBP_URL_PREFIX = FORTRESS_LAYER_URL_PREFIX
FORTRESS_LAYERS = {
    "strategium": "1-Strategium.png",
    "armory": "2-Armory.png",
    "apothecarion": "3-Apothecarion.png",
    "reclusiam": "4-Reclusiam.png",
    "black-vault": "5-Black Vault.png",
    "librarius": "6-Librarius.png",
    "dueling-grounds": "7-Dueling Grounds.png",
    "company-primus": "8-Primus Company Hall.png",
    "company-secundus": "9-Secundus Company Hall.png",
    "company-tertius": "10-Tertius Company Hall.png",
    "company-quartus": "11-Quartus Company Hall.png",
    "company-quintus": "12-Quintus Company Hall.png",
    "flight-deck": "13-Flight Deck.png",
    "vehicle-bays": "14-Vehicle Bays.png",
    "astropathic-choir": "15-Astropathic Choir.png",
}
FORMATION_SYMBOLS = {
    "armory": "Armory.png",
    "apothecarion": "Apothecarion.png",
    "librarius": "Librarians.png",
    "reclusiam": "Reclusiam.png",
    "black_vault": "Recon.png",
    "hall_of_blades": "Watch_Blades.png",
}
FORMATION_SYMBOLS_DIR = ROOT / "assets"
FORMATION_SYMBOL_THUMBS_DIR = WEB_ASSETS_DIR / "formation-symbols"
FORMATION_SYMBOL_URL_PREFIX = "/assets/formation-symbols/"
MAX_AWARDS_PER_MEMBER = 40
HOST = os.getenv("STRATEGIUM_HOST", "127.0.0.1")
PORT = int(os.getenv("STRATEGIUM_PORT", "8787"))
BOT_SHARED_SECRET = os.getenv("STRATEGIUM_BOT_SHARED_SECRET", "")
SESSION_SECRET = os.getenv("STRATEGIUM_SESSION_SECRET", "")
DISCORD_CLIENT_ID = os.getenv("DISCORD_OAUTH_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_OAUTH_REDIRECT_URI", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
DISCORD_INVITE_URL = os.getenv("STRATEGIUM_DISCORD_INVITE_URL", "")
ALLOWED_ORIGIN = os.getenv("STRATEGIUM_ALLOWED_ORIGIN", "http://127.0.0.1:8787").rstrip(
    "/"
)
SECURE_COOKIES = os.getenv("STRATEGIUM_SECURE_COOKIES", "0") == "1"
BACKSTORY_MAX_WORDS = 400
BACKSTORY_MAX_CHARS = 2400
SESSION_TTL_SECONDS = int(os.getenv("STRATEGIUM_SESSION_TTL_SECONDS", str(60 * 60 * 24 * 7)))
MAX_JSON_BODY_BYTES = 1024 * 1024
MAX_AAR_SUBMISSION_BYTES = 34 * 1024 * 1024
MAX_AAR_CONCURRENT_SUBMISSIONS = 2
_AAR_SUBMISSION_SLOTS = threading.BoundedSemaphore(MAX_AAR_CONCURRENT_SUBMISSIONS)
BOT_AAR_INTAKE_URL = os.getenv(
    "STRATEGIUM_BOT_AAR_INTAKE_URL",
    "http://127.0.0.1:8080/v1/aar/submissions",
)
BOT_AAR_SHARED_SECRET = os.getenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "")
BOT_AAR_ALLOWED_HOSTS = frozenset(
    host.strip().lower().rstrip(".")
    for host in os.getenv("STRATEGIUM_BOT_AAR_ALLOWED_HOSTS", "").split(",")
    if host.strip()
)
REACH_MAX_NODES = 500
REACH_MAX_EDGES = 2000
REACH_MAX_DIRECTIVES = 500
REACH_STATUSES = {
    "unassigned", "distributed", "recruiting", "deployed", "completed", "failed", "lapsed"
}
PAGE_PATHS = {"/", "/submit-aar", "/reach", "/record-of-blood", "/rank-guide"}

SECURITY_LOG = logging.getLogger("strategium.security")

_LOCK = threading.RLock()
_GEOGRAPHY_CACHE: tuple[Path, int, int, bytes] | None = None


def _normalize_origin(value: str) -> str:
    if not value:
        return ""
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not scheme or not host:
        return ""
    port = parsed.port
    if port is not None:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def _origin_matches(request_origin: str, allowed_origin: str) -> bool:
    request_value = _normalize_origin(request_origin)
    allowed_value = _normalize_origin(allowed_origin)
    return bool(request_value and allowed_value and request_value == allowed_value)


def _bot_aar_intake_target(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not 0 < port < 65536:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.path != "/v1/aar/submissions" or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
        is_private = address.is_loopback or address.is_private or address.is_link_local
    except ValueError:
        is_private = host == "localhost" or host.endswith(".localhost")
    if parsed.scheme == "http":
        return value if is_private else None
    if parsed.scheme == "https":
        return value if is_private or host in BOT_AAR_ALLOWED_HOSTS else None
    return None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _discord_invite_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    if port not in (None, 443):
        return ""
    host = (parsed.hostname or "").lower()
    if host == "discord.gg":
        valid_path = re.fullmatch(r"/[A-Za-z0-9-]+/?", parsed.path)
    elif host == "discord.com":
        valid_path = re.fullmatch(r"/invite/[A-Za-z0-9-]+/?", parsed.path)
    else:
        return ""
    return value.strip().rstrip("/") if valid_path else ""


def _request_is_secure(headers: Any) -> bool:
    forwarded = (headers.get("X-Forwarded-Proto") or "").lower()
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first == "https":
            return True
    return (headers.get("X-Forwarded-Ssl") or "").lower() == "on"


def _cookie_flags(secure: bool) -> str:
    flags = ["Path=/", "HttpOnly", "SameSite=Lax"]
    if secure:
        flags.append("Secure")
    return "; " + "; ".join(flags)


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, value: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _geography_bytes() -> bytes:
    global _GEOGRAPHY_CACHE
    with _LOCK:
        stat = GEOGRAPHY_PATH.stat()
        key = (GEOGRAPHY_PATH, stat.st_mtime_ns, stat.st_size)
        if _GEOGRAPHY_CACHE is None or _GEOGRAPHY_CACHE[:3] != key:
            _GEOGRAPHY_CACHE = (*key, _json_bytes(load_geography(GEOGRAPHY_PATH)))
        return _GEOGRAPHY_CACHE[3]


def _signature(body: bytes) -> str:
    return hmac.new(BOT_SHARED_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _session_value(user: dict[str, str]) -> str:
    user = {**user, "exp": int(time.time()) + SESSION_TTL_SECONDS}
    payload = base64.urlsafe_b64encode(_json_bytes(user)).decode("ascii").rstrip("=")
    digest = hmac.new(
        SESSION_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{digest}"


def _session_user(value: str) -> dict[str, str] | None:
    try:
        payload, supplied = value.rsplit(".", 1)
        expected = hmac.new(
            SESSION_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        user = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(user, dict) or not user.get("id"):
            return None
        if int(user.get("exp", 0)) <= int(time.time()):
            return None
        return user
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _discord_request(
    path: str, method: str = "GET", form: dict[str, str] | None = None, token: str = ""
) -> Any:
    url = "https://discord.com/api/v10" + path
    body = (
        urllib.parse.urlencode(form or {}).encode("utf-8") if form is not None else None
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": "DiscordBot (https://github.com/wfj-dev/strategium, 1.0)",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Discord API {error.code} {error.reason}: {error_body}"
        ) from error


def _validate_members(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("members"), list):
        raise ValueError("members must be an array")
    members = []
    for member in payload["members"]:
        if (
            not isinstance(member, dict)
            or not member.get("id")
            or not member.get("name")
        ):
            continue
        member = dict(member)
        member["awards"] = _validate_awards(member.get("awards"))
        members.append(member)
    return members


def _validate_awards(value: Any) -> list[dict[str, str]]:
    awards = []
    for award in _list(value, MAX_AWARDS_PER_MEMBER):
        if not isinstance(award, dict):
            continue
        name = _text(award.get("name"), 80)
        ribbon = _text(award.get("ribbon"), 80)
        if not name:
            continue
        if _ribbon_path(ribbon) is None:
            SECURITY_LOG.warning("award dropped: ribbon image %r not found in %s", ribbon, RIBBONS_DIR)
            continue
        awards.append({"name": name, "ribbon": ribbon})
    return awards


def _ribbon_path(name: str) -> Path | None:
    if not name.endswith(".png") or "/" in name or "\\" in name or name.startswith("."):
        return None
    candidate = (RIBBONS_DIR / name).resolve()
    if candidate.parent != RIBBONS_DIR.resolve() or not candidate.is_file():
        return None
    return candidate


def _pauldron_path(name: str) -> Path | None:
    match = re.fullmatch(r"[A-Za-z][A-Za-z ]*\.(png|webp)", name)
    if not match:
        return None
    directory = PAULDRON_THUMBS_DIR if match.group(1) == "webp" else PAULDRONS_DIR
    candidate = (directory / name).resolve()
    if candidate.parent != directory.resolve() or not candidate.is_file():
        return None
    return candidate


def _existing(path: Path | None) -> Path | None:
    return path if path is not None and path.is_file() else None


def _static_asset_path(path: str) -> Path | None:
    """Resolve an allowlisted media URL to a file, or None."""
    fixed = {
        AMBIENCE_URL: AMBIENCE_PATH,
        RECORD_WALL_URL: RECORD_WALL_PATH,
        JERICHO_SYMBOL_URL: JERICHO_SYMBOL_PATH,
        JERICHO_SYMBOL_WEBP_URL: JERICHO_SYMBOL_WEBP_PATH,
        INQUISITORIAL_ROSETTE_URL: INQUISITORIAL_ROSETTE_PATH,
        INQUISITORIAL_ROSETTE_WEBP_URL: INQUISITORIAL_ROSETTE_WEBP_PATH,
        DISCORD_MARK_URL: DISCORD_MARK_PATH,
        FORTRESS_MAP_URL: FORTRESS_MAP_PATH,
        FORTRESS_MAP_WEBP_URL: FORTRESS_MAP_WEBP_PATH,
        REACH_BACKGROUND_WEBP_URL: REACH_BACKGROUND_WEBP_PATH,
        REACH_STARFIELD_WEBP_URL: REACH_STARFIELD_WEBP_PATH,
        REACH_SECTOR_EDGES_URL: REACH_SECTOR_EDGES_PATH,
        FORTRESS_HOVER_MASK_URL: FORTRESS_HOVER_MASK_PATH,
    }
    if path in fixed:
        return _existing(fixed[path])
    if path.startswith("/assets/galactic-map/"):
        filename = path.removeprefix("/assets/galactic-map/")
        if filename not in {"base.webp", "sectors.png"} and not re.fullmatch(r"(?:[1-9]|1[0-9]|2[0-9])-(?:secure|critical|lost)\.webp", filename):
            return None
        return _existing(WEB_ASSETS_DIR / "galactic-map" / filename)
    if path.startswith("/assets/map-icons/"):
        filename = path.removeprefix("/assets/map-icons/")
        allowed = {"planet", "station", "forge_world", "shrine_world", "penal_world", "mining_world", "fortress_world", "feral_world", "agri_world", "frontier_world", "hive_world", "pleasure_world", "death_world", "war_world", "dead_world", "watch_fortress", "special"}
        allowed.update(f"star_{classification}" for classification in "obafgkm")
        if filename not in {f"{key}.webp" for key in allowed}:
            return None
        return _existing(WEB_ASSETS_DIR / "map-icons" / filename)
    if path.startswith(PAULDRON_URL_PREFIX):
        return _pauldron_path(urllib.parse.unquote(path[len(PAULDRON_URL_PREFIX):]))
    if path.startswith(RANK_URL_PREFIX):
        return _existing(RANK_IMAGES.get(urllib.parse.unquote(path[len(RANK_URL_PREFIX):])))
    if path.startswith(RANK_CARD_URL_PREFIX):
        return _existing(RANK_CARD_IMAGES.get(urllib.parse.unquote(path[len(RANK_CARD_URL_PREFIX):])))
    if path.startswith(FORTRESS_LAYER_URL_PREFIX):
        key, _, suffix = path[len(FORTRESS_LAYER_URL_PREFIX):].rpartition(".")
        filename = FORTRESS_LAYERS.get(key)
        if not filename or suffix not in {"png", "webp"}:
            return None
        return _existing(FORTRESS_LAYERS_DIR / Path(filename).with_suffix("." + suffix))
    if path.startswith(FORMATION_SYMBOL_URL_PREFIX):
        key, _, suffix = path[len(FORMATION_SYMBOL_URL_PREFIX):].rpartition(".")
        filename = FORMATION_SYMBOLS.get(key)
        if not filename:
            return None
        if suffix == "webp":
            return _existing(FORMATION_SYMBOL_THUMBS_DIR / Path(filename).with_suffix(".webp"))
        return _existing(FORMATION_SYMBOLS_DIR / filename) if suffix == "png" else None
    return None


def _text(value: Any, limit: int = 120) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if abs(value) < 1e6 else 0.0


def _list(value: Any, limit: int) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


def _text_list(value: Any, limit: int = 20, length: int = 120) -> list[str]:
    return [item for item in (_text(v, length) for v in _list(value, limit)) if item]


def _validate_reach(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("reach must be an object")
    nodes = []
    for node in _list(payload.get("nodes"), REACH_MAX_NODES):
        if isinstance(node, dict) and _text(node.get("id")):
            nodes.append({
                "id": _text(node.get("id")),
                "type": _text(node.get("type"), 40),
                "region": _text(node.get("region"), 60),
                "x": _number(node.get("x")),
                "y": _number(node.get("y")),
                "gamePlanet": node.get("gamePlanet") is True,
            })
    node_ids = {node["id"] for node in nodes}
    edges = []
    for edge in _list(payload.get("edges"), REACH_MAX_EDGES):
        if not isinstance(edge, dict):
            continue
        source, target = _text(edge.get("source")), _text(edge.get("target"))
        if source in node_ids and target in node_ids:
            edges.append({"source": source, "target": target, "proximity": _text(edge.get("proximity"), 20)})
    directives = []
    for item in _list(payload.get("directives"), REACH_MAX_DIRECTIVES):
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status"), 20)
        node = _text(item.get("node"))
        if status not in REACH_STATUSES or node not in node_ids:
            continue
        company = item.get("company")
        stratagems = item.get("stratagems") if isinstance(item.get("stratagems"), dict) else {}
        directives.append({
            "id": _text(item.get("id"), 40),
            "code": _text(item.get("code"), 40),
            "name": _text(item.get("name")),
            "node": node,
            "worldType": _text(item.get("worldType"), 40),
            "mission": _text(item.get("mission")),
            "mode": _text(item.get("mode"), 40),
            "classification": _text(item.get("classification"), 60),
            "requirementTier": _text(item.get("requirementTier"), 40),
            "requiredRoles": _text_list(item.get("requiredRoles")),
            "stratagems": {
                "positive": _text_list(stratagems.get("positive")),
                "negative": _text_list(stratagems.get("negative")),
            },
            "intelLapse": item.get("intelLapse") is True,
            "briefing": _text(item.get("briefing"), 2000),
            "status": status,
            "company": company if isinstance(company, int) and not isinstance(company, bool) and 1 <= company <= 5 else None,
            "killTeam": _text(item.get("killTeam")) or None,
            "participants": [p for p in _text_list(item.get("participants"), 50, 24) if p.isdigit()],
            "generatedAt": _text(item.get("generatedAt"), 40) or None,
            "deadline": _text(item.get("deadline"), 40) or None,
            "completedAt": _text(item.get("completedAt"), 40) or None,
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "directives": directives,
        "rep": max(-2.0, min(2.0, _number(payload.get("rep")))),
    }


def _backstory_error(text: str) -> str | None:
    character_count = len(text)
    if character_count > BACKSTORY_MAX_CHARS:
        return (
            f"Backstory is too long: {character_count:,}/{BACKSTORY_MAX_CHARS:,} "
            "characters. You're not that guy."
        )
    word_count = len(text.split())
    if word_count > BACKSTORY_MAX_WORDS:
        return (
            f"Backstory is too long: {word_count:,}/{BACKSTORY_MAX_WORDS:,} words. "
            "You're not that guy."
        )
    return None


class StrategiumHandler(BaseHTTPRequestHandler):
    server_version = "Strategium/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(
        self, status: int, payload: Any, headers: dict[str, str] | None = None
    ) -> None:
        body = payload if isinstance(payload, bytes) else _json_bytes(payload)
        request_origin = self.headers.get("Origin", "")
        if request_origin and not _origin_matches(request_origin, ALLOWED_ORIGIN):
            cors_origin = None
        else:
            cors_origin = ALLOWED_ORIGIN
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-Strategium-Signature, X-CSRF-Token, X-AAR-Idempotency-Key",
            )
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS"
            )
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> tuple[bytes, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError("request body too large")
        body = self.rfile.read(length)
        return body, json.loads(body.decode("utf-8"))

    def _cookie_user(self) -> dict[str, str] | None:
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookies.get("strategium_session")
        if not morsel or not SESSION_SECRET:
            return None
        return _session_user(morsel.value)

    def _cookie_value(self, name: str) -> str:
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookies.get(name)
        return morsel.value if morsel else ""

    def _csrf_valid(self, user: dict[str, str]) -> bool:
        supplied = self.headers.get("X-CSRF-Token", "")
        expected = str(user.get("csrf") or "")
        return bool(supplied and expected and hmac.compare_digest(supplied, expected))

    def do_OPTIONS(self) -> None:
        self._send(HTTPStatus.NO_CONTENT, {})

    def do_HEAD(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in PAGE_PATHS:
            body = (ROOT / "jericho-strategium.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        if path == AMBIENCE_URL and not AMBIENCE_PATH.is_file():
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("X-Asset-Available", "false")
            self.end_headers()
            return
        self._send_media(_static_asset_path(path), head=True)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in PAGE_PATHS:
            self._send_page()
        elif parsed.path == "/health":
            self._send(HTTPStatus.OK, {"ok": True})
        elif parsed.path == "/api/roster":
            self._send(HTTPStatus.OK, self._merged_roster())
        elif parsed.path == "/api/chapter-lore":
            reference = _load_json(CHAPTERS_REFERENCE_PATH, {})
            chapters = reference.get("chapters", []) if isinstance(reference, dict) else []
            lore = {
                chapter["name"]: chapter["lore_summary"]
                for chapter in chapters
                if isinstance(chapter, dict)
                and isinstance(chapter.get("name"), str)
                and isinstance(chapter.get("lore_summary"), str)
            } if isinstance(chapters, list) else {}
            self._send(HTTPStatus.OK, lore, {"Cache-Control": "no-cache"})
        elif parsed.path == "/api/reach":
            with _LOCK:
                reach = _load_json(REACH_PATH, {})
            self._send(HTTPStatus.OK, reach if isinstance(reach, dict) else {}, {"Cache-Control": "no-cache"})
        elif parsed.path == "/api/geography":
            try:
                self._send(HTTPStatus.OK, _geography_bytes(), {"Cache-Control": "no-cache"})
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                SECURITY_LOG.exception("geography read failed")
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "geography_unavailable"})
        elif parsed.path == "/api/site-config":
            self._send(
                HTTPStatus.OK,
                {"discordInviteUrl": _discord_invite_url(DISCORD_INVITE_URL)},
                {"Cache-Control": "no-cache"},
            )
        elif parsed.path.startswith(RIBBON_URL_PREFIX):
            self._send_ribbon(urllib.parse.unquote(parsed.path[len(RIBBON_URL_PREFIX):]))
        elif parsed.path.startswith("/assets/"):
            self._send_media(_static_asset_path(parsed.path))
        elif parsed.path == "/api/auth/discord/start":
            self._discord_start()
        elif parsed.path == "/api/auth/logout":
            self._send(
                HTTPStatus.OK,
                {"ok": True},
                {
                    "Set-Cookie": f"strategium_session=; Max-Age=0{_cookie_flags(SECURE_COOKIES or _request_is_secure(self.headers))}"
                },
            )
        elif parsed.path == "/api/auth/discord/callback":
            self._discord_callback(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/me":
            self._get_me()
        elif parsed.path == "/api/me/backstory":
            self._get_backstory()
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _send_ribbon(self, name: str) -> None:
        path = _ribbon_path(name)
        if path is None:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_media(self, path: Path | None, head: bool = False) -> None:
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = {".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml", ".mp3": "audio/mpeg"}[path.suffix]
        stat = path.stat()
        modified = int(stat.st_mtime)
        try:
            since = email.utils.parsedate_to_datetime(self.headers.get("If-Modified-Since", "")).timestamp()
        except (TypeError, ValueError):
            since = None
        status = HTTPStatus.NOT_MODIFIED if since is not None and since >= modified else HTTPStatus.OK
        self.send_response(status)
        if status == HTTPStatus.OK:
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(stat.st_size))
        # Short lifetime plus revalidation so regenerated art reaches browsers without URL versioning.
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Last-Modified", email.utils.formatdate(modified, usegmt=True))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head and status == HTTPStatus.OK:
            self.wfile.write(path.read_bytes())

    def _send_page(self) -> None:
        body = (ROOT / "jericho-strategium.html").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/internal/roster/snapshot":
            self._receive_snapshot()
            return
        if path == "/api/aar-submissions":
            self._forward_aar_submission()
            return
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _forward_aar_submission(self) -> None:
        user = self._cookie_user()
        if not user:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "login_required"})
            return
        if not _origin_matches(self.headers.get("Origin", ""), ALLOWED_ORIGIN) or not self._csrf_valid(user):
            self._send(HTTPStatus.FORBIDDEN, {"error": "csrf_failed"})
            return
        if not BOT_AAR_SHARED_SECRET:
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "aar_submission_unavailable"})
            return
        intake_url = _bot_aar_intake_target(BOT_AAR_INTAKE_URL)
        if not intake_url:
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "aar_submission_unavailable"})
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data;"):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "multipart_required"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_AAR_SUBMISSION_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if content_length > MAX_AAR_SUBMISSION_BYTES else HTTPStatus.BAD_REQUEST, {"error": "invalid_submission_size"})
            return
        idempotency_key = self.headers.get("X-AAR-Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", idempotency_key):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_idempotency_key"})
            return

        if not _AAR_SUBMISSION_SLOTS.acquire(blocking=False):
            self._send(HTTPStatus.TOO_MANY_REQUESTS, {"error": "aar_upload_capacity"}, {"Retry-After": "5"})
            return
        try:
            self._forward_aar_submission_body(user, idempotency_key, content_type, content_length, intake_url)
        finally:
            _AAR_SUBMISSION_SLOTS.release()

    def _forward_aar_submission_body(
        self,
        user: dict[str, str],
        idempotency_key: str,
        content_type: str,
        content_length: int,
        intake_url: str,
    ) -> None:
        body = self.rfile.read(content_length)
        if len(body) != content_length:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "incomplete_submission"})
            return
        timestamp = str(int(time.time()))
        body_digest = hashlib.sha256(body).hexdigest()
        signed = f"{timestamp}\n{idempotency_key}\n{user['id']}\n{body_digest}".encode()
        signature = hmac.new(BOT_AAR_SHARED_SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            intake_url,
            data=body,
            headers={
                "Content-Type": content_type,
                "X-Strategium-AAR-Timestamp": timestamp,
                "X-Strategium-AAR-Idempotency-Key": idempotency_key,
                "X-Strategium-AAR-Signature": signature,
                "X-Strategium-User-ID": str(user["id"]),
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler)
            with opener.open(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self._send(response.status, payload)
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"error": "aar_submission_failed"}
            self._send(error.code, payload)
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            SECURITY_LOG.warning("AAR intake bridge unavailable user=%s", user.get("id"))
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "aar_submission_unavailable"})

    def do_PATCH(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/api/me/backstory":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._set_backstory()

    def _merged_members(self) -> list[dict[str, Any]]:
        with _LOCK:
            snapshot = _load_json(ROSTER_PATH, {"members": []})
            backstories = _load_json(BACKSTORIES_PATH, {})
        members = snapshot.get("members", []) if isinstance(snapshot, dict) else []
        merged = []
        for member in members:
            item = dict(member)
            item["backstory"] = (backstories.get(str(member.get("id"))) or {}).get(
                "backstory"
            )
            merged.append(item)
        return merged

    def _merged_roster(self) -> dict[str, Any]:
        with _LOCK:
            snapshot = _load_json(ROSTER_PATH, {"members": []})
            backstories = _load_json(BACKSTORIES_PATH, {})
        if not isinstance(snapshot, dict):
            return {"members": []}
        members = []
        for member in snapshot.get("members", []):
            item = dict(member)
            item["backstory"] = (backstories.get(str(member.get("id"))) or {}).get(
                "backstory"
            )
            members.append(item)
        return {
            "members": members,
            "killTeams": snapshot.get("killTeams") or {},
            "directiveStats": snapshot.get("directiveStats") or {},
        }

    def _receive_snapshot(self) -> None:
        if not BOT_SHARED_SECRET:
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE, {"error": "bot secret not configured"}
            )
            return
        try:
            body, payload = self._read_json()
            supplied = self.headers.get("X-Strategium-Signature", "")
            if not hmac.compare_digest(supplied, _signature(body)):
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "invalid signature"})
                return
            members = _validate_members(payload)
            snapshot = {
                "generatedAt": payload.get("generatedAt"),
                "members": members,
                "killTeams": payload.get("killTeams")
                if isinstance(payload.get("killTeams"), dict)
                else {},
                "directiveStats": payload.get("directiveStats")
                if isinstance(payload.get("directiveStats"), dict)
                else {},
            }
            reach = None
            if "reach" in payload:
                try:
                    reach = {**_validate_reach(payload["reach"]), "generatedAt": _text(payload.get("generatedAt"), 40) or None}
                except ValueError as error:
                    SECURITY_LOG.warning("reach snapshot skipped: %s client=%s", error, self.client_address[0])
            with _LOCK:
                _save_json(ROSTER_PATH, snapshot)
                if reach is not None:
                    _save_json(REACH_PATH, reach)
            self._send(HTTPStatus.OK, {"ok": True, "memberCount": len(members)})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            SECURITY_LOG.warning("roster snapshot rejected: %s client=%s", error, self.client_address[0])
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _get_backstory(self) -> None:
        user = self._cookie_user()
        if not user:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "login_required"})
            return
        with _LOCK:
            backstories = _load_json(BACKSTORIES_PATH, {})
        self._send(
            HTTPStatus.OK,
            {"backstory": (backstories.get(user["id"]) or {}).get("backstory", "")},
        )

    def _get_me(self) -> None:
        user = self._cookie_user()
        self._send(HTTPStatus.OK, {"authenticated": bool(user), "user": user, "csrf": user.get("csrf") if user else None})

    def _set_backstory(self) -> None:
        user = self._cookie_user()
        if not user:
            SECURITY_LOG.warning("backstory update denied: unauthenticated client=%s", self.client_address[0])
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "login_required"})
            return
        origin_valid = _origin_matches(self.headers.get("Origin", ""), ALLOWED_ORIGIN)
        csrf_valid = self._csrf_valid(user)
        if not origin_valid or not csrf_valid:
            SECURITY_LOG.warning(
                "backstory update denied: origin_valid=%s csrf_valid=%s user=%s client=%s",
                origin_valid,
                csrf_valid,
                user.get("id"),
                self.client_address[0],
            )
            self._send(HTTPStatus.FORBIDDEN, {"error": "csrf_failed"})
            return
        try:
            _, payload = self._read_json()
            text = payload.get("backstory") if isinstance(payload, dict) else None
            if not isinstance(text, str):
                raise ValueError("backstory must be a string")
            text = text.strip()
            error = _backstory_error(text)
            if error:
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": error})
                return
            with _LOCK:
                backstories = _load_json(BACKSTORIES_PATH, {})
                if text:
                    backstories[user["id"]] = {"backstory": text}
                else:
                    backstories.pop(user["id"], None)
                _save_json(BACKSTORIES_PATH, backstories)
            self._send(HTTPStatus.OK, {"backstory": text})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _discord_start(self) -> None:
        if not DISCORD_CLIENT_ID or not DISCORD_REDIRECT_URI or not SESSION_SECRET:
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE, {"error": "oauth_not_configured"}
            )
            return
        state = secrets.token_urlsafe(24)
        query = urllib.parse.urlencode(
            {
                "client_id": DISCORD_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": DISCORD_REDIRECT_URI,
                "scope": "identify guilds",
                "state": state,
            }
        )
        secure = SECURE_COOKIES or _request_is_secure(self.headers)
        headers = {
            "Location": f"https://discord.com/oauth2/authorize?{query}",
            "Set-Cookie": f"strategium_oauth_state={state}{_cookie_flags(secure)}",
        }
        self._send(HTTPStatus.FOUND, {"ok": True}, headers)

    def _discord_callback(self, query: dict[str, list[str]]) -> None:
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        if (
            not code
            or not state
            or not hmac.compare_digest(
                state, self._cookie_value("strategium_oauth_state")
            )
            or not DISCORD_CLIENT_ID
            or not DISCORD_CLIENT_SECRET
            or not DISCORD_REDIRECT_URI
        ):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_oauth_callback"})
            return
        try:
            token = _discord_request(
                "/oauth2/token",
                method="POST",
                form={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DISCORD_REDIRECT_URI,
                },
            )
            user = _discord_request("/users/@me", token=token["access_token"])
            guilds = _discord_request("/users/@me/guilds", token=token["access_token"])
            if DISCORD_GUILD_ID and not any(
                str(guild.get("id")) == DISCORD_GUILD_ID for guild in guilds
            ):
                self._send(HTTPStatus.FORBIDDEN, {"error": "guild_membership_required"})
                return
            session = _session_value(
                {
                    "id": str(user["id"]),
                    "name": str(user.get("global_name") or user.get("username") or ""),
                    "csrf": secrets.token_urlsafe(32),
                }
            )
            secure = SECURE_COOKIES or _request_is_secure(self.headers)
            self._send(
                HTTPStatus.FOUND,
                {"ok": True},
                {
                    "Location": "/",
                    "Set-Cookie": f"strategium_session={session}; Max-Age={SESSION_TTL_SECONDS}{_cookie_flags(secure)}",
                },
            )
        except (
            KeyError,
            OSError,
            json.JSONDecodeError,
            urllib.error.URLError,
            RuntimeError,
        ) as error:
            SECURITY_LOG.exception("OAuth exchange failed")
            self._send(
                HTTPStatus.BAD_GATEWAY,
                {"error": "oauth_exchange_failed"},
            )


def main() -> None:
    if not BOT_SHARED_SECRET:
        raise SystemExit("STRATEGIUM_BOT_SHARED_SECRET is required")
    if not SESSION_SECRET:
        raise SystemExit("STRATEGIUM_SESSION_SECRET is required")
    if (
        not ALLOWED_ORIGIN
        or ALLOWED_ORIGIN == "http://127.0.0.1:8787"
        and HOST != "127.0.0.1"
    ):
        pass
    server = ThreadingHTTPServer((HOST, PORT), StrategiumHandler)
    print(f"Strategium backend listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
