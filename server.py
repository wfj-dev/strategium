"""Strategium backend for bot snapshots, roster reads, and self-owned backstories."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookies
import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


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
HOST = os.getenv("STRATEGIUM_HOST", "127.0.0.1")
PORT = int(os.getenv("STRATEGIUM_PORT", "8787"))
BOT_SHARED_SECRET = os.getenv("STRATEGIUM_BOT_SHARED_SECRET", "")
SESSION_SECRET = os.getenv("STRATEGIUM_SESSION_SECRET", "")
DISCORD_CLIENT_ID = os.getenv("DISCORD_OAUTH_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_OAUTH_REDIRECT_URI", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
ALLOWED_ORIGIN = os.getenv("STRATEGIUM_ALLOWED_ORIGIN", "http://127.0.0.1:8787").rstrip(
    "/"
)
SECURE_COOKIES = os.getenv("STRATEGIUM_SECURE_COOKIES", "0") == "1"
BACKSTORY_MAX_WORDS = 400
BACKSTORY_MAX_CHARS = 2400

_LOCK = threading.RLock()


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


def _signature(body: bytes) -> str:
    return hmac.new(BOT_SHARED_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _session_value(user: dict[str, str]) -> str:
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
        return user if isinstance(user, dict) and user.get("id") else None
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
        members.append(member)
    return members


def _backstory_error(text: str) -> str | None:
    if len(text) > BACKSTORY_MAX_CHARS:
        return f"backstory exceeds {BACKSTORY_MAX_CHARS} characters"
    if len(text.split()) > BACKSTORY_MAX_WORDS:
        return f"backstory exceeds {BACKSTORY_MAX_WORDS} words"
    return None


class StrategiumHandler(BaseHTTPRequestHandler):
    server_version = "Strategium/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(
        self, status: int, payload: Any, headers: dict[str, str] | None = None
    ) -> None:
        body = _json_bytes(payload)
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
                "Authorization, Content-Type, X-Strategium-Signature",
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
        if length > 1_000_000:
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

    def do_OPTIONS(self) -> None:
        self._send(HTTPStatus.NO_CONTENT, {})

    def do_HEAD(self) -> None:
        if urllib.parse.urlparse(self.path).path == "/":
            body = (ROOT / "jericho-strategium.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_page()
        elif parsed.path == "/health":
            self._send(HTTPStatus.OK, {"ok": True})
        elif parsed.path == "/api/roster":
            self._send(HTTPStatus.OK, self._merged_roster())
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

    def _send_page(self) -> None:
        body = (ROOT / "jericho-strategium.html").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/internal/roster/snapshot":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._receive_snapshot()

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
            with _LOCK:
                _save_json(ROSTER_PATH, snapshot)
            self._send(HTTPStatus.OK, {"ok": True, "memberCount": len(members)})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
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
        self._send(HTTPStatus.OK, {"authenticated": bool(user), "user": user})

    def _set_backstory(self) -> None:
        user = self._cookie_user()
        if not user:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "login_required"})
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
                }
            )
            secure = SECURE_COOKIES or _request_is_secure(self.headers)
            self._send(
                HTTPStatus.FOUND,
                {"ok": True},
                {
                    "Location": "/",
                    "Set-Cookie": f"strategium_session={session}{_cookie_flags(secure)}",
                },
            )
        except (
            KeyError,
            OSError,
            json.JSONDecodeError,
            urllib.error.URLError,
            RuntimeError,
        ) as error:
            self._send(
                HTTPStatus.BAD_GATEWAY,
                {"error": "oauth_exchange_failed", "detail": str(error)},
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
