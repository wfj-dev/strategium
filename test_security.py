import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server
from scripts import build_web_assets
from server import (
    BACKSTORY_MAX_CHARS,
    BACKSTORY_MAX_WORDS,
    _backstory_error,
    _cookie_flags,
    _discord_invite_url,
    _origin_matches,
    _pauldron_path,
    _request_is_secure,
    _ribbon_path,
    _session_user,
    _session_value,
    _validate_awards,
    _validate_reach,
)


@pytest.fixture(autouse=True)
def _clear_aar_access_cache():
    server._AAR_ACCESS_CACHE.clear()
    yield
    server._AAR_ACCESS_CACHE.clear()


def test_server_silences_client_disconnects_but_reports_real_errors(capsys) -> None:
    httpd = server.StrategiumServer(("127.0.0.1", 0), server.StrategiumHandler)
    try:
        for error, expect_output in ((BrokenPipeError(), False), (ConnectionResetError(), False), (ValueError("boom"), True)):
            try:
                raise error
            except Exception:
                httpd.handle_error(None, ("127.0.0.1", 1))
            assert bool(capsys.readouterr().err) is expect_output
    finally:
        httpd.server_close()


def test_aar_access_caches_success_and_briefly_caches_bridge_failure(monkeypatch) -> None:
    calls = []
    responses = [
        {"allowed": False, "available": False, "display_name": "", "max_file_bytes": 1},
        {"allowed": True, "display_name": "Brother", "max_file_bytes": 1},
    ]

    def fake_fetch(user_id):
        calls.append(user_id)
        return responses[len(calls) - 1]

    clock = [1000.0]
    monkeypatch.setattr(server, "_fetch_aar_access", fake_fetch)
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])

    assert server._aar_access("42")["available"] is False
    assert server._aar_access("42")["available"] is False
    assert len(calls) == 1
    clock[0] += server.AAR_ACCESS_FAILURE_TTL_SECONDS + 1
    assert server._aar_access("42")["display_name"] == "Brother"
    clock[0] += server.AAR_ACCESS_TTL_SECONDS - 1
    assert server._aar_access("42")["display_name"] == "Brother"
    assert len(calls) == 2


def test_me_falls_back_to_session_name_when_bridge_unavailable(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {
        "allowed": False, "available": False, "display_name": "", "max_file_bytes": 1,
    })
    cookie = _session_value({"id": "42", "name": "Jules", "csrf": "csrf-token"})
    request = urllib.request.Request(local_site + "/api/me", headers={"Cookie": f"strategium_session={cookie}"})
    with urllib.request.urlopen(request) as response:
        payload = json.loads(response.read())
    assert payload["user"]["name"] == "Jules"
    assert payload["degraded"] is True


def test_roster_response_is_cached_until_files_change(local_site, tmp_path, monkeypatch) -> None:
    roster = tmp_path / "roster_snapshot.json"
    backstories = tmp_path / "backstories.json"
    roster.write_text('{"members":[{"id":"1","name":"A"}]}', encoding="utf-8")
    monkeypatch.setattr(server, "ROSTER_PATH", roster)
    monkeypatch.setattr(server, "BACKSTORIES_PATH", backstories)
    monkeypatch.setattr(server, "_ROSTER_CACHE", None)
    loads = []
    original_load = server._load_json
    monkeypatch.setattr(server, "_load_json", lambda path, default: loads.append(path) or original_load(path, default))
    for _ in range(2):
        with urllib.request.urlopen(local_site + "/api/roster") as response:
            assert json.loads(response.read())["members"][0]["name"] == "A"
    assert len(loads) == 2
    backstories.write_text('{"1":{"backstory":"Hello"}}', encoding="utf-8")
    with urllib.request.urlopen(local_site + "/api/roster") as response:
        assert json.loads(response.read())["members"][0]["backstory"] == "Hello"
    assert len(loads) == 4


def test_origin_matches_exact_allowed_origin() -> None:
    assert _origin_matches("http://127.0.0.1:8787", "http://127.0.0.1:8787")
    assert _origin_matches("http://127.0.0.1:8787/", "http://127.0.0.1:8787")
    assert not _origin_matches("https://evil.example", "http://127.0.0.1:8787")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("http://127.0.0.1:8080/v1/aar/submissions", True),
        ("http://localhost:9123/v1/aar/submissions", True),
        ("http://bot.internal:8080/v1/aar/submissions", False),
        ("https://bot.internal/v1/aar/submissions", False),
        ("https://8.8.8.8/v1/aar/submissions", False),
        ("https://192.168.1.22/v1/aar/submissions", True),
        ("http://127.0.0.1:8080/other", False),
        ("http://user@127.0.0.1:8080/v1/aar/submissions", False),
    ],
)
def test_bot_aar_intake_target_requires_loopback_http_or_https(target: str, expected: bool) -> None:
    assert bool(server._bot_aar_intake_target(target)) is expected


def test_bot_aar_intake_target_allows_configured_private_https_host(monkeypatch) -> None:
    monkeypatch.setattr(server, "BOT_AAR_ALLOWED_HOSTS", frozenset({"bot.internal"}))
    assert server._bot_aar_intake_target("https://bot.internal/v1/aar/submissions")


def test_bot_aar_shared_secret_prefers_existing_bot_variable(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", "existing-bot-secret")
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "aar-specific-secret")

    assert server._bot_aar_shared_secret() == "existing-bot-secret"


def test_aar_access_returns_discord_effective_upload_limit(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", "test-secret")
    monkeypatch.setattr(server, "BOT_AAR_INTAKE_URL", "http://127.0.0.1:8080/v1/aar/submissions")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"allowed":true,"guild_member":true,"display_name":"Watch Member","max_file_bytes":26214400}'

    class FakeOpener:
        def open(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(server.urllib.request, "build_opener", lambda *_args: FakeOpener())

    access = server._aar_access("42")

    assert access["allowed"] is True
    assert access["max_file_bytes"] == 25 * 1024 * 1024


def test_cookie_flags_include_secure_when_required() -> None:
    flags = _cookie_flags(secure=True)
    assert "; Secure" in flags
    assert "SameSite=Lax" in flags


def test_war_read_and_mutations_require_login(local_site):
    for method in ("GET", "POST"):
        request = urllib.request.Request(local_site + "/api/war", data=b"{}" if method == "POST" else None, method=method)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 401


def test_war_mutation_requires_csrf_and_uses_authenticated_identity(local_site, monkeypatch):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    cookie = _session_value({"id": "42", "name": "Member", "csrf": "csrf-token"})
    captured = []
    monkeypatch.setattr(server, "_war_request", lambda user, payload: captured.append((user, payload)) or (200, {"version": 1}))
    body = json.dumps({"action": "commit", "user_id": "999"}).encode()
    headers = {"Cookie": f"strategium_session={cookie}", "Origin": local_site, "Content-Type": "application/json"}
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(local_site + "/api/war", data=body, headers=headers))
    assert error.value.code == 403
    assert captured == []
    headers["X-CSRF-Token"] = "csrf-token"
    with urllib.request.urlopen(urllib.request.Request(local_site + "/api/war", data=body, headers=headers)) as response:
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
    assert captured[0][0] == "42"


def test_member_correction_requests_require_csrf_but_not_editor_permission(local_site, monkeypatch):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    monkeypatch.setattr(server, "_aar_access", lambda user: {"allowed": True, "aar_management": {"can_edit": False, "can_delete": False}})
    captured = []
    monkeypatch.setattr(server, "_aar_management_request", lambda *args: captured.append(args) or (201, {"ok": True, "status": "pending"}))
    cookie = _session_value({"id": "42", "name": "Brother", "csrf": "csrf-token"})
    headers = {"Cookie": f"strategium_session={cookie}", "Origin": local_site, "Content-Type": "application/json"}
    body = json.dumps({"record_id": "123", "submission": {"armory_data": 3}, "original_record_hash": "a" * 64}).encode()
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(local_site + "/api/aar-edit-requests", data=body, headers=headers))
    assert error.value.code == 403
    assert not captured
    headers["X-CSRF-Token"] = "csrf-token"
    with urllib.request.urlopen(urllib.request.Request(local_site + "/api/aar-edit-requests", data=body, headers=headers)) as response:
        assert response.status == 201
    assert captured[0][0:2] == ("42", "request-edit")
    for path in ("/api/aar-management", "/manage-aars", "/api/aar-management/edit-requests/" + "a" * 24 + "/reject"):
        request = urllib.request.Request(local_site + path, data=b'{"note":"Not permitted"}' if path.endswith("reject") else None, headers=headers)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
    assert len(captured) == 1


def test_member_owned_aar_read_and_delete_request_keep_staff_routes_private(local_site, monkeypatch):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    monkeypatch.setattr(server, "_aar_access", lambda user: {"allowed": True, "aar_management": {"can_edit": False, "can_delete": False}})
    captured = []
    monkeypatch.setattr(server, "_aar_management_request", lambda *args: captured.append(args) or (200, {"id": "123"}))
    cookie = _session_value({"id": "42", "name": "Brother", "csrf": "csrf-token"})
    headers = {"Cookie": f"strategium_session={cookie}", "Origin": local_site, "Content-Type": "application/json"}
    with urllib.request.urlopen(urllib.request.Request(local_site + "/api/aar-own/records/123", headers=headers)) as response:
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
    assert captured[0] == ("42", "own-record", {}, "123")
    deletion = local_site + "/api/aar-own/records/123/delete-request"
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(deletion, data=b'{"reason":"Duplicate report"}', headers=headers))
    assert error.value.code == 403
    headers["X-CSRF-Token"] = "csrf-token"
    with urllib.request.urlopen(urllib.request.Request(deletion, data=b'{"reason":"Duplicate report"}', headers=headers)) as response:
        assert response.status == 200
    assert captured[-1][0:2] == ("42", "own-delete-request")
    for path in ("/api/aar-management/records/123", "/api/aar-management/records/123/delete-confirm"):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(local_site + path, data=b'{}' if path.endswith("confirm") else None, headers=headers))
        assert error.value.code == 403


def test_request_is_secure_uses_forwarded_proto() -> None:
    headers = {"X-Forwarded-Proto": "https"}
    assert _request_is_secure(headers)
    assert not _request_is_secure({"X-Forwarded-Proto": "http"})


def test_management_proxy_reads_bounded_complete_responses(monkeypatch):
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", "review-response-test-secret")
    monkeypatch.setattr(server, "BOT_AAR_INTAKE_URL", "http://127.0.0.1:8080/v1/aar/submissions")
    response_body = json.dumps({"pending_deletions": [{"reason": "x" * 150000}]}).encode()
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            return response_body[:limit]
    monkeypatch.setattr(server.urllib.request, "build_opener", lambda *args: type("Opener", (), {"open": lambda *args, **kwargs: Response()})())
    assert server._aar_management_request("42", "list", {}) == (200, json.loads(response_body))
    response_body = b"x" * (server.MAX_AAR_MANAGEMENT_RESPONSE_BYTES + 10)
    assert server._aar_management_request("42", "list", {}) is None


def test_staff_preview_proxy_binds_review_to_selected_record(local_site, monkeypatch):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "_aar_access", lambda user: {"allowed": True, "aar_management": {"can_edit": True, "can_delete": False}})
    captured = []
    monkeypatch.setattr(server, "_aar_management_request", lambda *args: captured.append(args) or (200, {"id": "123", "review": {"status": "pending"}}))
    cookie = _session_value({"id": "42", "name": "Reviewer", "csrf": "csrf-token"})
    path = local_site + "/api/aar-management/records/123?review_type=edit&review_id=" + "a" * 24
    with urllib.request.urlopen(urllib.request.Request(path, headers={"Cookie": f"strategium_session={cookie}"})) as response:
        assert response.status == 200
    assert captured == [("42", "records", {"review_type": "edit", "review_id": "a" * 24}, "123")]


def test_staff_deletion_decline_requires_csrf_and_editor_access(local_site, monkeypatch):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    capabilities = {"can_edit": False, "can_delete": False}
    monkeypatch.setattr(server, "_aar_access", lambda user: {"allowed": True, "aar_management": capabilities})
    captured = []
    monkeypatch.setattr(server, "_aar_management_request", lambda *args: captured.append(args) or (200, {"status": "cancelled"}))
    cookie = _session_value({"id": "42", "name": "Reviewer", "csrf": "csrf-token"})
    headers = {"Cookie": f"strategium_session={cookie}", "Origin": local_site, "Content-Type": "application/json", "X-CSRF-Token": "csrf-token"}
    path = local_site + "/api/aar-management/records/123/delete-reject"
    for editor, csrf, expected in [(False, "csrf-token", 403), (True, "wrong", 403), (True, "csrf-token", 200)]:
        capabilities["can_edit"] = editor
        headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(path, data=b'{"note":"Keep the valid report"}', headers=headers)
        if expected == 200:
            with urllib.request.urlopen(request) as response:
                assert response.status == expected
        else:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            assert error.value.code == expected
    assert len(captured) == 1
    assert captured[0][0:2] == ("42", "delete-reject")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://discord.gg/example", "https://discord.gg/example"),
        ("https://discord.com/invite/example", "https://discord.com/invite/example"),
        ("https://discord.gg/B5pkZhcHzK", "https://discord.gg/B5pkZhcHzK"),
        ("http://discord.gg/example", ""),
        ("https://discord.gg:bad/example", ""),
        ("https://discord.gg:99999/example", ""),
        ("https://evil.example/invite/example", ""),
        ("javascript:alert(1)", ""),
        ("", ""),
    ],
)
def test_discord_invite_url_accepts_only_discord_https_invites(value: str, expected: str) -> None:
    assert _discord_invite_url(value) == expected


def test_oxblood_cursor_asset_is_allowlisted_and_served(local_site) -> None:
    path = server._static_asset_path("/assets/oxblood-cursor.png")
    assert path == server.OXBLOOD_CURSOR_PATH

    with urllib.request.urlopen(local_site + "/assets/oxblood-cursor.png") as response:
        assert response.headers["Content-Type"] == "image/png"
        assert len(response.read()) < 20_000


def test_session_contains_expiry_and_csrf_token() -> None:
    token = _session_value({"id": "42", "name": "Test", "csrf": "csrf-token"})
    user = _session_user(token)
    assert user is not None
    assert user["csrf"] == "csrf-token"
    assert user["exp"] > int(time.time())


def test_backstory_accepts_character_and_word_boundaries() -> None:
    assert _backstory_error("x" * BACKSTORY_MAX_CHARS) is None
    assert _backstory_error(" ".join(["x"] * BACKSTORY_MAX_WORDS)) is None


def test_backstory_character_error_includes_actual_and_maximum() -> None:
    actual = BACKSTORY_MAX_CHARS + 1
    assert _backstory_error("x" * actual) == (
        f"Backstory is too long: {actual:,}/{BACKSTORY_MAX_CHARS:,} characters. "
        "You're not that guy."
    )


def test_backstory_word_error_includes_actual_and_maximum() -> None:
    actual = BACKSTORY_MAX_WORDS + 1
    assert _backstory_error(" ".join(["x"] * actual)) == (
        f"Backstory is too long: {actual:,}/{BACKSTORY_MAX_WORDS:,} words. "
        "You're not that guy."
    )


def test_reach_validation_drops_unknown_fields_and_bad_references() -> None:
    reach = _validate_reach(
        {
            "nodes": [{"id": "Avarax", "x": 10, "y": "bad", "secret": 1}, {"x": 3}],
            "edges": [{"source": "Avarax", "target": "Nowhere"}],
            "directives": [
                {
                    "id": "a",
                    "node": "Avarax",
                    "status": "deployed",
                    "company": 9,
                    "participants": ["123", "<script>"],
                    "forumThreadId": 5,
                },
                {"id": "b", "node": "Avarax", "status": "hacked"},
                {"id": "c", "node": "Nowhere", "status": "deployed"},
            ],
            "rep": 99,
        }
    )
    assert reach["nodes"] == [
        {
            "id": "Avarax",
            "type": "",
            "region": "",
            "x": 10.0,
            "y": 0.0,
            "gamePlanet": False,
        }
    ]
    assert reach["edges"] == []
    [directive] = reach["directives"]
    assert directive["company"] is None
    assert directive["participants"] == ["123"]
    assert "forumThreadId" not in directive
    assert reach["rep"] == 2.0


def test_reach_validation_tolerates_wrong_container_types() -> None:
    reach = _validate_reach({"nodes": {"a": 1}, "edges": "x", "directives": None})
    assert reach == {"nodes": [], "edges": [], "directives": [], "rep": 0.0}


def test_ribbon_path_rejects_traversal_and_non_png() -> None:
    assert _ribbon_path("1-Order Omega.png") is not None
    for name in [
        "../server.py",
        "..%2Fserver.py",
        "../assets/ribbons/1-Order Omega.png",
        "/etc/passwd.png",
        "sub\\x.png",
        ".hidden.png",
        "server.py",
        "missing.png",
    ]:
        assert _ribbon_path(name) is None


@pytest.fixture
def local_site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.StrategiumHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_discord_login_cookie_persists_for_session_ttl(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "DISCORD_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "DISCORD_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server, "DISCORD_REDIRECT_URI", "https://example.test/callback")

    def fake_discord_request(path, method="GET", form=None, token=""):
        if path == "/oauth2/token":
            return {"access_token": "access-token"}
        if path == "/users/@me":
            return {"id": "42", "username": "Test"}
        if path == "/users/@me/guilds":
            return []
        raise AssertionError(f"unexpected Discord API path: {path}")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, response, code, message, headers, new_url):
            return None

    monkeypatch.setattr(server, "_discord_request", fake_discord_request)
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        f"{local_site}/api/auth/discord/callback?code=test-code&state=test-state",
        headers={"Cookie": "strategium_oauth_state=test-state"},
    )
    with pytest.raises(urllib.error.HTTPError) as response:
        opener.open(request)

    assert response.value.code == 302
    cookie = response.value.headers["Set-Cookie"]
    assert f"Max-Age={server.SESSION_TTL_SECONDS}" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_discord_oauth_state_cookie_remains_session_scoped(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "DISCORD_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "DISCORD_REDIRECT_URI", "https://example.test/callback")
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, response, code, message, headers, new_url):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as response:
        opener.open(local_site + "/api/auth/discord/start")

    assert response.value.code == 302
    cookie = response.value.headers["Set-Cookie"]
    assert cookie.startswith("strategium_oauth_state=")
    assert "Max-Age" not in cookie


def test_record_of_blood_direct_route(local_site) -> None:
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + "/record-of-blood", method=method)) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert (b"Record of Blood" in response.read()) is (method == "GET")


def test_geography_api_serves_validated_hierarchy(local_site) -> None:
    with urllib.request.urlopen(local_site + "/api/geography") as response:
        payload = json.loads(response.read())

    assert response.status == 200
    assert payload["schemaVersion"] == 1
    assert len(payload["sectors"]) == 29
    assert all(sector["status"] == "secure" for sector in payload["sectors"])
    assert payload["landmarks"]["fortressBodyId"] == "watch_fortress_jericho"


@pytest.mark.parametrize("filename", ["base.webp", "sectors.png", "leviathan-tendril.webp", "1-secure.webp", "29-critical.webp", "4-lost.webp"])
def test_galactic_map_assets_are_allowlisted(local_site, filename) -> None:
    with urllib.request.urlopen(local_site + "/assets/galactic-map/" + filename) as response:
        assert response.status == 200
        assert response.read()


@pytest.mark.parametrize("filename", ["0-secure.webp", "30-secure.webp", "1-contested.webp", "geometry.json", "%2e%2e/server.py", "1-secure.png", "leviathan-tendril.png", "../Leviathan_Tendril.png"])
def test_galactic_map_rejects_unknown_assets(filename) -> None:
    assert server._static_asset_path("/assets/galactic-map/" + filename) is None


@pytest.mark.parametrize("classification", list("obafgkm"))
def test_spectral_icons_are_served_from_explicit_allowlist(local_site, classification) -> None:
    with urllib.request.urlopen(local_site + f"/assets/map-icons/star_{classification}.webp") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "image/webp"
        assert response.read()


@pytest.mark.parametrize("filename", ["star_q.webp", "star_o.png", "../star_o.webp"])
def test_spectral_icon_unknown_names_and_paths_are_rejected(filename) -> None:
    assert server._static_asset_path("/assets/map-icons/" + filename) is None


def test_geography_api_caches_and_reloads_validated_data(local_site, tmp_path, monkeypatch) -> None:
    geography_path = tmp_path / "reach_geography.json"
    original_data = server.GEOGRAPHY_PATH.read_bytes()
    geography_path.write_bytes(original_data)
    monkeypatch.setattr(server, "GEOGRAPHY_PATH", geography_path)
    original_load = server.load_geography
    loads = []

    def counted_load(path):
        loads.append(path)
        return original_load(path)

    monkeypatch.setattr(server, "load_geography", counted_load)
    for _ in range(2):
        with urllib.request.urlopen(local_site + "/api/geography") as response:
            assert json.loads(response.read())["schemaVersion"] == 1
    assert loads == [geography_path]

    geography_path.write_text('{"schemaVersion":2}', encoding="utf-8")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + "/api/geography")
    assert error.value.code == 503

    geography_path.write_bytes(original_data)
    with urllib.request.urlopen(local_site + "/api/geography") as response:
        assert json.loads(response.read())["schemaVersion"] == 1
    assert loads == [geography_path] * 3


def test_site_config_exposes_sanitized_discord_invite(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "DISCORD_INVITE_URL", "https://discord.gg/example")
    with urllib.request.urlopen(local_site + "/api/site-config") as response:
        payload = json.loads(response.read())

    assert response.status == 200
    assert payload == {"discordInviteUrl": "https://discord.gg/example"}


def test_chapter_lore_endpoint_returns_only_named_summaries(local_site, tmp_path, monkeypatch) -> None:
    reference = tmp_path / "chapters.json"
    reference.write_text(
        '{"chapters":[{"name":"Example Chapter","lore_summary":"A concise record."},'
        '{"name":"No Summary","lore_summary":3},{"lore_summary":"Missing name"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "CHAPTERS_REFERENCE_PATH", reference)
    with urllib.request.urlopen(local_site + "/api/chapter-lore") as response:
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.read() == b'{"Example Chapter":"A concise record."}'


def test_record_wall_is_served_only_at_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    wall = tmp_path / "record of blood wall.png"
    monkeypatch.setattr(server, "RECORD_WALL_PATH", wall)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.RECORD_WALL_URL)
    assert error.value.code == 404
    wall.write_bytes(b"wall")
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + server.RECORD_WALL_URL, method=method)) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["Content-Length"] == "4"
            assert response.read() == (b"wall" if method == "GET" else b"")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + "/assets/record-of-blood-wall.png/other")
    assert error.value.code == 404


def test_jericho_symbol_is_served_only_at_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    symbol = tmp_path / "jericho symbol.png"
    monkeypatch.setattr(server, "JERICHO_SYMBOL_PATH", symbol)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.JERICHO_SYMBOL_URL)
    assert error.value.code == 404
    symbol.write_bytes(b"symbol")
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + server.JERICHO_SYMBOL_URL, method=method)) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["Content-Length"] == "6"
            assert response.read() == (b"symbol" if method == "GET" else b"")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.JERICHO_SYMBOL_URL + "/other")
    assert error.value.code == 404


def test_inquisitorial_rosette_is_served_only_at_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    rosette = tmp_path / "Inquisitorial_Rosette.png"
    monkeypatch.setattr(server, "INQUISITORIAL_ROSETTE_PATH", rosette)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.INQUISITORIAL_ROSETTE_URL)
    assert error.value.code == 404
    rosette.write_bytes(b"rosette")
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + server.INQUISITORIAL_ROSETTE_URL, method=method)) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["Content-Length"] == "7"
            assert response.read() == (b"rosette" if method == "GET" else b"")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.INQUISITORIAL_ROSETTE_URL + "/other")
    assert error.value.code == 404


def test_discord_mark_is_served_only_at_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    mark = tmp_path / "discord-mark.svg"
    monkeypatch.setattr(server, "DISCORD_MARK_PATH", mark)
    mark.write_bytes(b"<svg></svg>")
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + server.DISCORD_MARK_URL, method=method)) as response:
            assert response.headers["Content-Type"] == "image/svg+xml"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.read() == (b"<svg></svg>" if method == "GET" else b"")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.DISCORD_MARK_URL + "/other")
    assert error.value.code == 404


def test_formation_symbols_are_allowlisted(local_site, tmp_path, monkeypatch) -> None:
    symbols = {
        "armory": "Armory.png",
        "apothecarion": "Apothecarion.png",
        "librarius": "Librarians.png",
        "reclusiam": "Reclusiam.png",
        "black_vault": "Recon.png",
        "hall_of_blades": "Watch_Blades.png",
    }
    monkeypatch.setattr(server, "FORMATION_SYMBOLS_DIR", tmp_path)
    monkeypatch.setattr(server, "FORMATION_SYMBOLS", symbols)
    for key, filename in symbols.items():
        (tmp_path / filename).write_bytes(key.encode("ascii"))
        for method in ("GET", "HEAD"):
            with urllib.request.urlopen(urllib.request.Request(local_site + f"/assets/formation-symbols/{key}.png", method=method)) as response:
                assert response.headers["Content-Type"] == "image/png"
                assert response.read() == (key.encode("ascii") if method == "GET" else b"")
    for path in ("unknown", "../server.py", "armory.png/extra"):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(local_site + "/assets/formation-symbols/" + path + ".png")
        assert error.value.code == 404


def test_fortress_map_is_served_only_at_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    fortress_map = tmp_path / "Watch_Fortress_Jericho_Map.png"
    monkeypatch.setattr(server, "FORTRESS_MAP_PATH", fortress_map)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.FORTRESS_MAP_URL)
    assert error.value.code == 404
    fortress_map.write_bytes(b"atlas")
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + server.FORTRESS_MAP_URL, method=method)) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["Content-Length"] == "5"
            assert response.read() == (b"atlas" if method == "GET" else b"")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.FORTRESS_MAP_URL + "/other")
    assert error.value.code == 404


def test_fortress_highlight_layers_are_allowlisted(local_site, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "FORTRESS_LAYERS_DIR", tmp_path)
    for key, filename in server.FORTRESS_LAYERS.items():
        (tmp_path / filename).write_bytes(key.encode("ascii"))
        for method in ("GET", "HEAD"):
            url = local_site + server.FORTRESS_LAYER_URL_PREFIX + key + ".png"
            with urllib.request.urlopen(urllib.request.Request(url, method=method)) as response:
                assert response.headers["Content-Type"] == "image/png"
                assert response.read() == (key.encode("ascii") if method == "GET" else b"")
    for path in ("unknown.png", "../server.py", "armory.png/extra"):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(local_site + server.FORTRESS_LAYER_URL_PREFIX + path)
        assert error.value.code == 404


def test_optimized_map_assets_are_served_and_allowlisted(local_site, tmp_path, monkeypatch) -> None:
    for attr, url in (("FORTRESS_MAP_WEBP_PATH", server.FORTRESS_MAP_WEBP_URL),
                      ("REACH_BACKGROUND_WEBP_PATH", server.REACH_BACKGROUND_WEBP_URL),
                      ("REACH_STARFIELD_WEBP_PATH", server.REACH_STARFIELD_WEBP_URL),
                      ("JERICHO_SYMBOL_WEBP_PATH", server.JERICHO_SYMBOL_WEBP_URL),
                      ("INQUISITORIAL_ROSETTE_WEBP_PATH", server.INQUISITORIAL_ROSETTE_WEBP_URL),
                      ("REACH_SECTOR_EDGES_PATH", server.REACH_SECTOR_EDGES_URL)):
        image = tmp_path / (attr + ".webp")
        image.write_bytes(b"webp")
        monkeypatch.setattr(server, attr, image)
        for method in ("GET", "HEAD"):
            with urllib.request.urlopen(urllib.request.Request(local_site + url, method=method)) as response:
                assert response.headers["Content-Type"] == "image/webp"
                assert response.read() == (b"webp" if method == "GET" else b"")
    monkeypatch.setattr(server, "FORTRESS_LAYERS_DIR", tmp_path)
    (tmp_path / server.FORTRESS_LAYERS["strategium"].replace(".png", ".webp")).write_bytes(b"layer")
    for method in ("GET", "HEAD"):
        url = local_site + server.FORTRESS_LAYER_WEBP_URL_PREFIX + "strategium.webp"
        with urllib.request.urlopen(urllib.request.Request(url, method=method)) as response:
            assert response.headers["Content-Type"] == "image/webp"
            assert response.read() == (b"layer" if method == "GET" else b"")
    for path in ("unknown.webp", "../server.py", "strategium.webp/extra"):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(local_site + server.FORTRESS_LAYER_WEBP_URL_PREFIX + path)
        assert error.value.code == 404


def test_fortress_hover_mask_is_served_only_at_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    mask = tmp_path / "atlas-hover-mask.png"
    monkeypatch.setattr(server, "FORTRESS_HOVER_MASK_PATH", mask)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.FORTRESS_HOVER_MASK_URL)
    assert error.value.code == 404
    mask.write_bytes(b"mask")
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + server.FORTRESS_HOVER_MASK_URL, method=method)) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.read() == (b"mask" if method == "GET" else b"")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.FORTRESS_HOVER_MASK_URL + "/other")
    assert error.value.code == 404


def test_media_revalidates_with_last_modified(local_site, tmp_path, monkeypatch) -> None:
    mask = tmp_path / "atlas-hover-mask.png"
    mask.write_bytes(b"mask")
    monkeypatch.setattr(server, "FORTRESS_HOVER_MASK_PATH", mask)
    with urllib.request.urlopen(local_site + server.FORTRESS_HOVER_MASK_URL) as response:
        last_modified = response.headers["Last-Modified"]
        assert response.headers["Cache-Control"] == "public, max-age=300"
    request = urllib.request.Request(local_site + server.FORTRESS_HOVER_MASK_URL, headers={"If-Modified-Since": last_modified})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 304
    os.utime(mask, (mask.stat().st_atime, mask.stat().st_mtime + 60))
    with urllib.request.urlopen(request) as response:
        assert response.read() == b"mask"


def test_pauldron_assets_are_allowlisted(local_site, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PAULDRONS_DIR", tmp_path)
    (tmp_path / "Blood Angels.png").write_bytes(b"image")
    (tmp_path / "Hawk Lords.png").write_bytes(b"png")
    assert _pauldron_path("Blood Angels.png") == tmp_path / "Blood Angels.png"
    for name, mime in (("Blood Angels.png", "image/png"), ("Hawk Lords.png", "image/png")):
        for method in ("GET", "HEAD"):
            with urllib.request.urlopen(urllib.request.Request(local_site + "/assets/pauldrons/" + urllib.parse.quote(name), method=method)) as response:
                assert response.headers["Content-Type"] == mime
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert bool(response.read()) is (method == "GET")
    monkeypatch.setattr(server, "PAULDRON_THUMBS_DIR", tmp_path)
    (tmp_path / "Mortifactors.webp").write_bytes(b"webp")
    with urllib.request.urlopen(local_site + "/assets/pauldrons/Mortifactors.webp") as response:
        assert response.headers["Content-Type"] == "image/webp"
        assert response.read() == b"webp"
    for name in ("../server.py", "test.svg", "Blood Angels.png/extra", "missing.png", "missing.webp", "%2e%2e%2fserver.py"):
        assert _pauldron_path(urllib.parse.unquote(name)) is None
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(local_site + "/assets/pauldrons/" + name.replace(" ", "%20"))
        assert error.value.code == 404


def test_pauldron_builder_prefers_root_level_future_chapter_art(tmp_path, monkeypatch) -> None:
    painted = tmp_path / "Painted Pauldrons"
    completed = painted / "Completed"
    completed.mkdir(parents=True)
    completed_art = completed / "Black Vipers.png"
    future_art = painted / "Black Vipers.png"
    completed_art.write_bytes(b"existing")
    future_art.write_bytes(b"future")
    monkeypatch.setattr(build_web_assets, "ASSETS", tmp_path)

    assert build_web_assets.pauldron_sources() == [future_art]


@pytest.mark.parametrize("chapter", ["Consecrators", "Doom Eagles", "Black Vipers"])
def test_new_chapter_pauldron_is_registered_and_served(local_site, chapter) -> None:
    page = (server.ROOT / "jericho-strategium.html").read_text(encoding="utf-8")
    art = page.split("const PAULDRON_ART =", 1)[1].split("].map(", 1)[0]
    assert f'"{chapter}"' in art
    with urllib.request.urlopen(local_site + "/assets/pauldrons/" + urllib.parse.quote(chapter + ".webp")) as response:
        assert response.headers["Content-Type"] == "image/webp"
        assert response.read().startswith(b"RIFF")


def test_black_vipers_chapter_has_site_lore_and_pauldron_mapping():
    page = (server.ROOT / "jericho-strategium.html").read_text(encoding="utf-8")
    chapters = page.split("const CHAPTERS =", 1)[1].split("function seededPick(", 1)[0]
    art = page.split("const PAULDRON_ART =", 1)[1].split("let bloodPageSize", 1)[0]
    lore = page.split("const CHAPTER_LORE_FALLBACKS =", 1)[1].split("};", 1)[0]
    assert '"Black Vipers"' in chapters
    assert '"Black Vipers"' in art
    for fact in (
        "Primaris-only", "Ultima Founding", "999.M41", "Vulkan",
        "no homeworld is known or claimed", "Sur Kgosi", "disappeared",
        "Cawl's agents", "red viper-head badge", "unrecorded", "pp. 63, 71",
    ):
        assert fact in lore


def test_rank_ribbons_serve_only_known_images(local_site, tmp_path, monkeypatch) -> None:
    ribbon = tmp_path / "1-Watch Brother.png"
    ribbon.write_bytes(b"rank")
    monkeypatch.setattr(server, "RANK_IMAGES", {"Watch Brother.png": ribbon})
    for method in ("GET", "HEAD"):
        url = local_site + server.RANK_URL_PREFIX + "Watch%20Brother.png"
        with urllib.request.urlopen(urllib.request.Request(url, method=method)) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.read() == (b"rank" if method == "GET" else b"")
    for name in ("Unknown.png", "%2e%2e%2fserver.py"):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(local_site + server.RANK_URL_PREFIX + name)
        assert error.value.code == 404


def test_rank_cards_serve_only_known_images(local_site, tmp_path, monkeypatch) -> None:
    card = tmp_path / "1-Watch_Brother.webp"
    card.write_bytes(b"rank card")
    monkeypatch.setattr(server, "RANK_CARD_IMAGES", {card.name: card})
    for method in ("GET", "HEAD"):
        url = local_site + server.RANK_CARD_URL_PREFIX + card.name
        with urllib.request.urlopen(urllib.request.Request(url, method=method)) as response:
            assert response.headers["Content-Type"] == "image/webp"
            assert response.read() == (b"rank card" if method == "GET" else b"")
    for name in ("missing.webp", "%2e%2e%2fserver.py"):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(local_site + server.RANK_CARD_URL_PREFIX + name)
        assert error.value.code == 404


def test_rank_guide_route_serves_the_site_page(local_site) -> None:
    with urllib.request.urlopen(local_site + "/rank-guide") as response:
        assert response.headers["Content-Type"].startswith("text/html")
        page = response.read()
    assert b'data-page="rank-guide"' in page
    assert b"Rank Guide" in page
    for text in (
        b"Promotion System", b"After Action Report Points", b"Crucible",
        b"20 operation points minus KIA", b"clamps KIA to 0-4", b"16-20 points", b"two to five brothers",
        b"KIA: 0", b"at least one other",
        b"beyond Watch Veteran", b"does not guarantee promotion",
        b"compliance check", b"4 weeks and 400", b"16 weeks and 1,600",
        b"1429318686447108300", b"1432804829364748319", b"1429303902343401575",
    ):
        assert text in page
    assert b"each brother who extracts" not in page


def test_submit_aar_direct_route_serves_the_site_page(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": True, "display_name": "Guild Nickname"})
    cookie = _session_value({"id": "42", "name": "AccountName", "csrf": "csrf-token"})
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + "/submit-aar", method=method, headers={"Cookie": f"strategium_session={cookie}"})) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            page = response.read()
            if method == "GET":
                assert b'data-page="submit-aar"' in page
                assert b"function renderSubmitAar()" in page
            else:
                assert page == b""


def test_manage_aars_direct_route_requires_editor_capability(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    cookie = _session_value({"id": "42", "name": "AccountName", "csrf": "csrf-token"})
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {
        "allowed": True, "display_name": "Guild Nickname", "aar_management": {"can_edit": False, "can_delete": False}
    })
    request = urllib.request.Request(local_site + "/manage-aars", headers={"Cookie": f"strategium_session={cookie}"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403

    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {
        "allowed": True, "display_name": "Guild Nickname", "aar_management": {"can_edit": True, "can_delete": False}
    })
    with urllib.request.urlopen(urllib.request.Request(local_site + "/manage-aars", headers={"Cookie": f"strategium_session={cookie}"})) as response:
        assert response.status == 200
        assert b'data-page="manage-aars"' in response.read()


def test_aar_submission_requires_login(local_site) -> None:
    request = urllib.request.Request(
        local_site + "/api/aar-submissions",
        data=b"multipart-body",
        headers={"Content-Type": "multipart/form-data; boundary=x"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 401
    assert json.loads(error.value.read())["error"] == "login_required"


def test_aar_submission_rejects_when_global_upload_slots_are_full(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": True, "display_name": "Guild Nickname"})
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "test-aar-secret")
    monkeypatch.setattr(server, "BOT_AAR_INTAKE_URL", "http://127.0.0.1:8080/v1/aar/submissions")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    token = _session_value({"id": "42", "name": "Test", "csrf": "csrf-token"})

    class FullBudget:
        def acquire(self, blocking=False):
            return False

        def release(self):
            raise AssertionError("full budget must not be released by rejected request")

    monkeypatch.setattr(server, "_AAR_SUBMISSION_SLOTS", FullBudget())
    request = urllib.request.Request(
        local_site + "/api/aar-submissions",
        data=b"multipart-body",
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Cookie": f"strategium_session={token}",
            "Origin": local_site,
            "X-CSRF-Token": "csrf-token",
            "X-AAR-Idempotency-Key": "site-submit-1234567890",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 429
    assert json.loads(error.value.read())["error"] == "aar_upload_capacity"


def test_aar_submission_rejects_invalid_csrf(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    token = _session_value({"id": "42", "name": "Test", "csrf": "expected-csrf"})
    request = urllib.request.Request(
        local_site + "/api/aar-submissions",
        data=b"multipart-body",
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Cookie": f"strategium_session={token}",
            "Origin": local_site,
            "X-CSRF-Token": "incorrect-csrf",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403
    assert json.loads(error.value.read())["error"] == "csrf_failed"


def test_aar_submission_forwards_signed_body_and_authenticated_user(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": True, "display_name": "Guild Nickname"})
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "test-aar-secret")
    monkeypatch.setattr(server, "BOT_AAR_INTAKE_URL", "http://127.0.0.1:8080/v1/aar/submissions")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    token = _session_value({"id": "42", "name": "Test", "csrf": "csrf-token"})
    body = b"multipart-body"
    idempotency_key = "site-submit-1234567890"
    forwarded = {}

    class FakeResponse:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok":true,"receipt_url":"https://discord.com/channels/1/2/3"}'

    class FakeOpener:
        def __init__(self, capture: bool):
            self.capture = capture

        def open(self, request, data=None, timeout=None):
            if self.capture:
                forwarded["request"] = request
                forwarded["timeout"] = timeout
                return FakeResponse()
            return original_build_opener().open(request, data, timeout)

    original_build_opener = server.urllib.request.build_opener
    monkeypatch.setattr(
        server.urllib.request,
        "build_opener",
        lambda *handlers: FakeOpener(server._NoRedirectHandler in handlers),
    )
    request = urllib.request.Request(
        local_site + "/api/aar-submissions",
        data=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Cookie": f"strategium_session={token}",
            "Origin": local_site,
            "X-CSRF-Token": "csrf-token",
            "X-AAR-Idempotency-Key": idempotency_key,
        },
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        payload = json.loads(response.read())

    sent = forwarded["request"]
    sent_headers = {key.lower(): value for key, value in sent.header_items()}
    timestamp = sent_headers["x-strategium-aar-timestamp"]
    digest = server.hashlib.sha256(body).hexdigest()
    signed = f"{timestamp}\n{idempotency_key}\n42\n{digest}".encode()
    expected = server.hmac.new(b"test-aar-secret", signed, server.hashlib.sha256).hexdigest()
    assert sent.data == body
    assert sent_headers["x-strategium-user-id"] == "42"
    assert sent_headers["x-strategium-aar-signature"] == expected
    assert forwarded["timeout"] == 60
    assert payload["receipt_url"] == "https://discord.com/channels/1/2/3"


def test_aar_member_search_forwards_signed_query_and_authenticated_user(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": True, "display_name": "Guild Nickname"})
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "test-aar-secret")
    monkeypatch.setattr(server, "BOT_AAR_INTAKE_URL", "http://127.0.0.1:8080/v1/aar/submissions")
    cookie = _session_value({"id": "42", "name": "Test", "csrf": "csrf-token"})
    forwarded = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            return b'{"members":[{"id":"99","name":"Jules Reserve"}],"has_more":false}'

    class FakeOpener:
        def open(self, request, data=None, timeout=None):
            if request.full_url.startswith(local_site):
                return original_build_opener().open(request, data=data, timeout=timeout)
            forwarded["request"] = request
            forwarded["timeout"] = timeout
            return FakeResponse()

    original_build_opener = server.urllib.request.build_opener
    previous_opener = server.urllib.request._opener
    server.urllib.request._opener = original_build_opener()
    monkeypatch.setattr(server.urllib.request, "build_opener", lambda *_handlers: FakeOpener())
    request = urllib.request.Request(
        local_site + "/api/aar-members?q=Jules",
        headers={"Cookie": f"strategium_session={cookie}"},
    )

    try:
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read())
    finally:
        server.urllib.request._opener = previous_opener

    sent = forwarded["request"]
    sent_headers = {key.lower(): value for key, value in sent.header_items()}
    body = sent.data
    timestamp = sent_headers["x-strategium-aar-timestamp"]
    digest = server.hashlib.sha256(body).hexdigest()
    signed = f"{timestamp}\nmembers-42\n42\n{digest}".encode()
    expected = server.hmac.new(b"test-aar-secret", signed, server.hashlib.sha256).hexdigest()
    assert payload == {"members": [{"id": "99", "name": "Jules Reserve"}], "has_more": False}
    assert json.loads(body) == {"query": "Jules"}
    assert sent_headers["x-strategium-user-id"] == "42"
    assert sent_headers["x-strategium-aar-signature"] == expected
    assert forwarded["timeout"] == 10


def test_aar_member_search_requires_login(local_site):
    request = urllib.request.Request(local_site + "/api/aar-members?q=Jules")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 401
    assert json.loads(error.value.read())["error"] == "login_required"


def test_aar_management_edit_proxy_signs_staff_request_and_requires_csrf(local_site, monkeypatch) -> None:
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {
        "allowed": True, "display_name": "Techmarine", "aar_management": {"can_edit": True, "can_delete": False}
    })
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "edit-proxy-secret")
    monkeypatch.setattr(server, "BOT_AAR_INTAKE_URL", "http://127.0.0.1:8080/v1/aar/submissions")
    cookie = _session_value({"id": "42", "name": "Test", "csrf": "csrf-token"})
    forwarded = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            return b'{"ok":true,"record_id":"123"}'

    class FakeOpener:
        def open(self, request, data=None, timeout=None):
            if request.full_url.startswith(local_site):
                return original_build_opener().open(request, data=data, timeout=timeout)
            forwarded["request"] = request
            forwarded["timeout"] = timeout
            return FakeResponse()

    original_build_opener = server.urllib.request.build_opener
    previous_opener = server.urllib.request._opener
    server.urllib.request._opener = original_build_opener()
    monkeypatch.setattr(server.urllib.request, "build_opener", lambda *_handlers: FakeOpener())
    body = b"multipart-edit-body"
    request_id = "staff-edit-request-123456"
    request = urllib.request.Request(
        local_site + "/api/aar-management/records/123",
        data=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Cookie": f"strategium_session={cookie}",
            "Origin": local_site,
            "X-CSRF-Token": "csrf-token",
            "X-AAR-Management-Request-ID": request_id,
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
            assert json.loads(response.read())["record_id"] == "123"
        sent = forwarded["request"]
        headers = {key.lower(): value for key, value in sent.header_items()}
        timestamp = headers["x-strategium-aar-timestamp"]
        digest = server.hashlib.sha256(body).hexdigest()
        signed = f"{timestamp}\nedit-123-{request_id}\n42\n{digest}".encode()
        expected = server.hmac.new(b"edit-proxy-secret", signed, server.hashlib.sha256).hexdigest()
        assert sent.data == body
        assert headers["x-strategium-user-id"] == "42"
        assert headers["x-aar-management-request-id"] == request_id
        assert headers["x-strategium-aar-signature"] == expected
        sent_request = sent

        denied = urllib.request.Request(
            local_site + "/api/aar-management/records/123", data=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=x",
                "Cookie": f"strategium_session={cookie}", "Origin": local_site,
                "X-CSRF-Token": "wrong", "X-AAR-Management-Request-ID": request_id,
            }, method="PATCH",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(denied)
        assert error.value.code == 403
        assert forwarded["request"] is sent_request
    finally:
        server.urllib.request._opener = previous_opener


def test_aar_management_request_uses_matching_private_bot_paths_and_signatures(monkeypatch) -> None:
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "management-path-secret")
    monkeypatch.setattr(server, "BOT_AAR_INTAKE_URL", "http://127.0.0.1:8080/v1/aar/submissions")
    forwarded = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            return b'{"ok":true}'

    class FakeOpener:
        def open(self, request, timeout=None):
            forwarded.append(request)
            return FakeResponse()

    monkeypatch.setattr(server.urllib.request, "build_opener", lambda *_handlers: FakeOpener())
    cases = [
        ("list", "", "/v1/aar/management/list"),
        ("records", "123", "/v1/aar/management/records/123"),
        ("own-record", "123", "/v1/aar/own/records/123"),
        ("own-delete-request", "123", "/v1/aar/own/records/123/delete-request"),
        ("delete-request", "123", "/v1/aar/management/records/123/delete-request"),
        ("delete-confirm", "123", "/v1/aar/management/records/123/delete-confirm"),
        ("delete-reject", "123", "/v1/aar/management/records/123/delete-reject"),
        ("edit-resume", "123", "/v1/aar/management/records/123/edit-resume"),
        ("reviews/0123456789abcdef01234567/acknowledge", "123", "/v1/aar/management/records/123/reviews/0123456789abcdef01234567/acknowledge"),
    ]
    for action, record_id, path in cases:
        assert server._aar_management_request("42", action, {}, record_id) == (200, {"ok": True})
        request = forwarded[-1]
        headers = {key.lower(): value for key, value in request.header_items()}
        assert request.full_url == "http://127.0.0.1:8080" + path
        timestamp = headers["x-strategium-aar-timestamp"]
        key = f"management-{action}-{record_id or 'list'}"
        digest = server.hashlib.sha256(request.data).hexdigest()
        signed = f"{timestamp}\n{key}\n42\n{digest}".encode()
        expected = server.hmac.new(b"management-path-secret", signed, server.hashlib.sha256).hexdigest()
        assert headers["x-strategium-aar-signature"] == expected


def test_ambience_is_optional_and_served_from_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    track = tmp_path / "fortress-ambience.mp3"
    monkeypatch.setattr(server, "AMBIENCE_PATH", track)
    with urllib.request.urlopen(urllib.request.Request(local_site + server.AMBIENCE_URL, method="HEAD")) as response:
        assert response.status == 204
        assert response.headers["X-Asset-Available"] == "false"
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_site + server.AMBIENCE_URL)
    assert error.value.code == 404
    track.write_bytes(b"audio")
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + server.AMBIENCE_URL, method=method)) as response:
            assert response.headers["Content-Type"] == "audio/mpeg"
            assert response.headers["Content-Length"] == "5"
            assert response.read() == (b"audio" if method == "GET" else b"")


def test_awards_validation_keeps_only_known_ribbons() -> None:
    awards = _validate_awards(
        [
            {"name": "The Order Omega", "ribbon": "1-Order Omega.png"},
            {"name": "Evil", "ribbon": "../server.py"},
            {"name": "", "ribbon": "1-Order Omega.png"},
            "junk",
        ]
    )
    assert awards == [{"name": "The Order Omega", "ribbon": "1-Order Omega.png"}]
    assert _validate_awards(None) == []


def test_validate_members_sanitizes_recent_aar_teammates() -> None:
    members = server._validate_members({
        "members": [{
            "id": "42",
            "name": "Brother FortyTwo",
            "recentAarTeammates": ["101", "101", "not-an-id", "102", "103", "104", "105", "106"],
        }]
    })

    assert members[0]["recentAarTeammates"] == ["101", "102", "103", "104", "105"]


@pytest.fixture
def evidence_session(local_site, monkeypatch):
    phone_limit = 12 * 1024 * 1024
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": True, "display_name": "Guild Nickname", "max_file_bytes": phone_limit})
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    monkeypatch.setattr(server, "_EVIDENCE_SESSIONS", {})
    monkeypatch.setattr(server, "_EVIDENCE_CREATE_TIMES", {})
    monkeypatch.setattr(server, "_EVIDENCE_CLEANUP_STARTED", True)
    cookie = _session_value({"id": "42", "name": "Test", "csrf": "csrf-token"})
    headers = {"Cookie": f"strategium_session={cookie}", "Origin": local_site, "X-CSRF-Token": "csrf-token"}
    request = urllib.request.Request(local_site + "/api/aar-evidence/session", data=b"", headers=headers, method="POST")
    with urllib.request.urlopen(request) as response:
        handoff = json.loads(response.read())
        assert response.headers["Cache-Control"] == "no-store"
    assert handoff["max_file_bytes"] == phone_limit
    assert f"max_file_bytes={phone_limit}" in handoff["upload_url"]
    fragment = urllib.parse.urlsplit(handoff["upload_url"]).fragment
    session_id, token = fragment.split(".")
    assert server._EVIDENCE_SESSIONS[session_id]["max_image_bytes"] == phone_limit
    assert handoff["qr"].startswith("data:image/png;base64,")
    return local_site, headers, session_id, token


def _valid_evidence_png() -> bytes:
    from PIL import Image

    buffer = server.io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_phone_evidence_upload_only_token_and_owner_retrieval(evidence_session):
    local_site, owner_headers, session_id, token = evidence_session
    image = _valid_evidence_png()
    path = local_site + f"/api/aar-evidence/{session_id}"
    upload = urllib.request.Request(path + "/upload", data=image, headers={
        "Origin": local_site, "Authorization": f"Bearer {token}", "Content-Type": "image/png"
    }, method="POST")
    with urllib.request.urlopen(upload) as response:
        assert response.status == 201
        image_id = json.loads(response.read())["image_id"]
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(path, headers={"Authorization": f"Bearer {token}"}))
    assert error.value.code == 401
    other_cookie = _session_value({"id": "99", "name": "Other", "csrf": "other"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(path, headers={"Cookie": f"strategium_session={other_cookie}"}))
    assert error.value.code == 404
    with urllib.request.urlopen(urllib.request.Request(path, headers=owner_headers)) as response:
        assert len(json.loads(response.read())["images"]) == 1
    with urllib.request.urlopen(urllib.request.Request(path + "/" + image_id, headers=owner_headers)) as response:
        assert response.read() == image
        assert response.headers["Cache-Control"] == "no-store"
    with urllib.request.urlopen(urllib.request.Request(path + "/close", data=b"", headers=owner_headers, method="POST")) as response:
        assert response.status == 200
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(upload)
    assert error.value.code == 403


def test_phone_evidence_upload_enforces_session_guild_limit(evidence_session):
    local_site, _owner_headers, session_id, token = evidence_session
    server._EVIDENCE_SESSIONS[session_id]["max_image_bytes"] = 4
    request = urllib.request.Request(
        local_site + f"/api/aar-evidence/{session_id}/upload",
        data=b"12345",
        headers={"Origin": local_site, "Authorization": f"Bearer {token}", "Content-Type": "image/png"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 413
    assert json.loads(error.value.read())["max_file_bytes"] == 4


def test_phone_evidence_link_expiry(evidence_session):
    local_site, headers, session_id, _token = evidence_session
    server._EVIDENCE_SESSIONS[session_id]["expires_at"] = time.time() - 1
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(local_site + f"/api/aar-evidence/{session_id}", headers=headers))
    assert error.value.code == 404
    assert session_id not in server._EVIDENCE_SESSIONS


def test_phone_link_creation_cooldown_precedes_qr_encoding(evidence_session, monkeypatch):
    import qrcode

    local_site, headers, _session_id, _token = evidence_session

    def unexpected_encode(_value):
        raise AssertionError("Rate-limited requests must not generate QR codes")

    monkeypatch.setattr(qrcode, "make", unexpected_encode)
    request = urllib.request.Request(local_site + "/api/aar-evidence/session", data=b"", headers=headers, method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 429


def test_phone_evidence_rejects_upload_when_owner_loses_pilot_access(evidence_session, monkeypatch):
    local_site, _headers, session_id, token = evidence_session
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": False, "display_name": "Guild Nickname"})
    request = urllib.request.Request(local_site + f"/api/aar-evidence/{session_id}/upload", data=_valid_evidence_png(), headers={
        "Origin": local_site, "Authorization": f"Bearer {token}", "Content-Type": "image/png"
    }, method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403
    assert server._EVIDENCE_SESSIONS[session_id]["images"] == {}


def test_phone_evidence_rejects_malformed_image(evidence_session):
    local_site, _headers, session_id, token = evidence_session
    request = urllib.request.Request(local_site + f"/api/aar-evidence/{session_id}/upload", data=b"\x89PNG\r\n\x1a\n", headers={
        "Origin": local_site, "Authorization": f"Bearer {token}", "Content-Type": "image/png"
    }, method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 400
    assert server._EVIDENCE_SESSIONS[session_id]["images"] == {}


def test_phone_evidence_upload_enforces_global_capacity(evidence_session, monkeypatch):
    local_site, _headers, session_id, token = evidence_session
    monkeypatch.setattr(server, "EVIDENCE_MAX_GLOBAL_BYTES", 1)
    request = urllib.request.Request(local_site + f"/api/aar-evidence/{session_id}/upload", data=_valid_evidence_png(), headers={
        "Origin": local_site, "Authorization": f"Bearer {token}", "Content-Type": "image/png"
    }, method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 429
    assert server._EVIDENCE_SESSIONS[session_id]["images"] == {}


def test_phone_evidence_creation_requires_login_and_csrf(local_site, monkeypatch):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    request = urllib.request.Request(local_site + "/api/aar-evidence/session", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 401
    cookie = _session_value({"id": "42", "name": "Test", "csrf": "csrf-token"})
    request.add_header("Cookie", f"strategium_session={cookie}")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


@pytest.mark.parametrize("path,method", [
    ("/submit-aar", "GET"), ("/submit-aar", "HEAD"),
    ("/api/aar-submissions", "POST"), ("/api/aar-evidence/session", "POST"),
])
def test_aar_pilot_rejects_logged_in_nonstaff(local_site, monkeypatch, path, method):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "ALLOWED_ORIGIN", local_site)
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": False, "display_name": "Guild Nickname"})
    cookie = _session_value({"id": "42", "name": "AccountName", "csrf": "csrf-token"})
    headers = {"Cookie": f"strategium_session={cookie}", "Origin": local_site, "X-CSRF-Token": "csrf-token"}
    request = urllib.request.Request(local_site + path, data=b"" if method == "POST" else None, headers=headers, method=method)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


def test_me_uses_guild_display_name_not_account_name(local_site, monkeypatch):
    monkeypatch.setattr(server, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(server, "_aar_access", lambda _user_id: {"allowed": True, "display_name": "Watch Techmarine Jules", "max_file_bytes": 24 * 1024 * 1024, "aar_management": {"can_edit": True, "can_delete": False}})
    cookie = _session_value({"id": "42", "name": "real-account-username", "csrf": "csrf-token"})
    with urllib.request.urlopen(urllib.request.Request(local_site + "/api/me", headers={"Cookie": f"strategium_session={cookie}"})) as response:
        body = response.read()
        payload = json.loads(body)
    assert payload["aar_allowed"] is True
    assert payload["aar_management"] == {"can_edit": True, "can_delete": False}
    assert payload["aar_max_file_bytes"] == 24 * 1024 * 1024
    assert payload["user"]["name"] == "Watch Techmarine Jules"
    assert b"real-account-username" not in body


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the war signup regression")
def test_locked_strike_ui_has_no_launch_step_and_prefills_normal_aar():
        source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
const handlers = {};
const navigation = [];
const consoleElement = {outerHTML:''};
const offer = {id: 'offer', attempt_id: 'GW-' + 'a'.repeat(32), name: 'Psychic Hunt', briefing: 'Support the operation', aar_mission: 'Inferno', aar_mission_value: 'pve_inferno', aar_mode: 'omega', aar_difficulty: '@Omega', status: 'locked', capacity: 5, required_rank: 'Watch Librarian', specialist_id: '43', participants: ['42','43','44','45','46']};
const context = {
    state: {auth: {authenticated: true}, selectedSector: 'alpha', geography: {sectors: [{id:'alpha',name:'Alpha'}],systems: []}},
    warView: {busy: false, error: '', planet: 'world', subsector: 'alpha', pool: 'omega', data: {version: 1, actor: {id: '42', roles: ['Watch Captain'], company_id: '1'}, campaigns: {}, commitments: {}, kill_teams: [], proposals: [], planets: [{id: 'world', name:'World',subsector_id:'alpha'}], boards: {world: [offer, {...offer,id:'vanilla-offer',name:'Vanilla only',aar_mode:'ops_strat',pool:'vanilla',capacity:3}]}, content: {objectives:29}, participant_names: {'42':'Captain','43':'Librarian'}}},
    COMPANY_NAMES: {'1':'Primus'}, escapeHtml: value=>String(value ?? ''), titleCase: value=>value, geographySystemDirectoryHtml:()=>'',
    aarDraft: {memberDirectory:new Map(), managementRecordId:'old', idempotencyKey:'old', reviewing:true},
    document: {addEventListener:(name,callback)=>{handlers[name]=callback;},getElementById:()=>consoleElement,querySelectorAll:()=>[]},
    setPage:async (...args)=>navigation.push(args), renderReach:()=>{throw new Error('Canvas must stay mounted');},
    loadWar:async()=>{},showSectorLevel:identity=>{context.state.selectedSector=identity;}
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('function warConsoleHtml()'),html.indexOf('async function refreshReach()')),context);
const markup = context.warConsoleHtml();
assert.match(markup,/Roster locked/);
assert.match(markup,/data-war-action="report"/);
assert.match(markup,/data-war-action="cancel"/);
assert.doesNotMatch(markup,/data-war-action="deploy"|data-war-action="ready"|coordinator/i);
assert.doesNotMatch(markup,/Vanilla only/);
assert.match(markup,/data-war-pool="omega" aria-pressed="true"/);
assert.match(markup,/1 locked/);
assert.match(markup,/0 available/);
assert.match(markup,/Game mission/);
assert.match(markup,/Briefing theme/);
assert.doesNotMatch(markup,/briefing types/);
assert.doesNotMatch(markup,/aria-label="Strike evidence review"/);
context.warView.data.actor.can_review=true;
context.warView.data.reviews=[{mission:'Inferno',aar_id:'99',reason:'Evidence reused',status:'pending',participants:['43'],strike_id:'held',receipt_url:'https://discord.com/channels/1/2/99'}];
assert.match(context.warConsoleHtml(),/data-war-action="review"/);
assert.match(context.warConsoleHtml(),/data-war-action="reverse"/);
assert.match(context.warConsoleHtml(),/Settled strike to reverse/);
context.warView.data.actor.can_review=false;
for(const [role,invade,commit] of [['Forgemaster',true,false],['Watch Master',true,true],['Watch Captain',false,true],['Watch Lieutenant',false,true],['Watch Sergeant',false,false],['Watch Brother',false,false]]){
    context.warView.data.actor.roles=[role];
    const controls=context.warConsoleHtml();
    assert.equal(controls.includes('data-war-action="invade"'),invade,role);
    assert.equal(controls.includes('data-war-action="commit"'),commit,role);
}
context.warView.data.actor.roles=['Watch Captain'];
(async()=>{
    await handlers.click({target:{closest:()=>({dataset:{warPool:'vanilla'}})}});
    assert.equal(context.warView.pool,'vanilla');
    assert.match(consoleElement.outerHTML,/Vanilla only/);
    await handlers.change({target:{closest:()=>({dataset:{warField:'planet_id'},value:'world'})}});
    assert.match(consoleElement.outerHTML,/war-console/);
    await handlers.change({target:{closest:()=>({dataset:{warField:'subsector_id'},value:'alpha'})}});
    assert.equal(context.state.selectedSector,'alpha');
    context.warView.planet='world';
    assert.match(context.warConsoleHtml(),/Vanilla only/);
    assert.doesNotMatch(context.warConsoleHtml(),/Psychic Hunt/);
    context.warView.pool='omega';
        const players=offer.participants;
        offer.participants=[];offer.status='available';
        context.warView.data.actor.watch_brother=false;
        assert.doesNotMatch(context.warConsoleHtml(),/data-war-action="join"/);
        context.warView.data.actor.watch_brother=true;
        assert.match(context.warConsoleHtml(),/data-war-action="join"/);
        offer.participants=players;offer.status='locked';
    await handlers.click({target:{closest:()=>({dataset:{warAction:'report',strike:'offer'}})}});
    assert.equal(context.aarDraft.strikeId,offer.attempt_id);
    assert.equal(context.aarDraft.mode,'omega');
    assert.equal(context.aarDraft.mission,'pve_inferno');
    assert.equal(context.aarDraft.difficulty,'@Omega');
    assert.deepEqual(Array.from(context.aarDraft.brothers),offer.participants);
    assert.equal(context.aarDraft.memberDirectory.get('43').name,'Librarian');
    assert.equal(context.aarDraft.managementRecordId,'');
    assert.equal(context.aarDraft.idempotencyKey,'');
    assert.deepEqual(navigation,[['submit-aar',true]]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for authenticated map initialization")
def test_first_reach_render_loads_roles_after_authentication():
    source = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
const calls=[];
const context={state:{auth:{authenticated:false}},wireEvents:()=>{},pageFromLocation:()=> 'reach',
 loadSavedSettings:async()=>{},loadData:async()=>{},loadSiteConfig:async()=>{},
 loadReach:async()=>{calls.push('geography');},loadAuth:async()=>{calls.push('auth');context.state.auth.authenticated=true;},
 loadWar:async()=>{assert(context.state.auth.authenticated);calls.push('war');},
 render:()=>{assert(calls.includes('war'));calls.push('render');},runCogitatorBoot:()=>{},refreshReach:()=>{},setInterval:()=>{},REACH_REFRESH_MS:30000};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('async function init()'),html.lastIndexOf('init();')),context);
context.init().then(()=>assert.deepEqual(calls,['geography','auth','war','render'])).catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_mobile_reach_keeps_chart_and_touch_navigation_visible():
    page = (server.ROOT / "jericho-strategium.html").read_text(encoding="utf-8")
    assert "(max-width: 900px) and (pointer: coarse)" in page
    assert ".reach-viewport { display: block; width: 100%; aspect-ratio: auto;" in page
    assert ".reach-viewport { display: none; }" not in page
    assert "data-mobile-sector-select" in page
    assert "data-mobile-map-focus" in page
    assert "data-mobile-details" in page
    assert '<details class="reach-mobile-index"><summary>Sector directory</summary>' in page


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for mobile map gestures")
def test_canvas_pinch_is_anchored_and_cancelled_gestures_do_not_select():
    source = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
const handlers={},selections=[];
const context={activePointers:new Map(),pinchGesture:null,multiPointerGesture:false,pointerId:null,previousPointer:null,dragDistance:0,zoomAnchor:null,
 width:400,height:300,state:{reachCanvasCamera:{x:0,y:0,targetX:0,targetY:0,zoom:1,targetZoom:1,baseZoom:1}},
 canvas:{addEventListener:(name,handler)=>{handlers[name]=handler;},setPointerCapture:()=>{}},viewport:{classList:{add:()=>{},remove:()=>{}}},tooltip:null,
 localPoint:event=>({x:event.clientX,y:event.clientY}),screenToWorld:(x,y)=>({x:context.state.reachCanvasCamera.x+(x-200)/context.state.reachCanvasCamera.zoom,y:context.state.reachCanvasCamera.y+(y-150)/context.state.reachCanvasCamera.zoom}),
 canvasClamp:(value,min,max)=>Math.min(max,Math.max(min,value)),clampToArt:(x,y)=>({x,y}),clearSectorSelection:()=>{},updateCanvasHover:()=>{},
 geography:{systems:[]},BODY_HOVER_RATIO:10,SYSTEM_HOVER_RATIO:10,geographySectorAt:()=>({id:'alpha'}),showSectorLevel:identity=>selections.push(identity)};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('  function pinchPoints()'),html.indexOf('  canvas.addEventListener("pointerleave"',html.indexOf('  function pinchPoints()'))),context);
const event=(identity,x,y)=>({pointerId:identity,clientX:x,clientY:y,button:0,pointerType:'touch'});
handlers.pointerdown(event(1,100,150));handlers.pointerdown(event(2,200,150));
handlers.pointermove(event(2,300,150));
assert.equal(context.state.reachCanvasCamera.zoom,2);
assert.equal(context.state.reachCanvasCamera.targetZoom,2);
assert.equal(context.state.reachCanvasCamera.x,-50);
handlers.pointerup(event(2,300,150));handlers.pointermove(event(1,110,150));handlers.pointerup(event(1,110,150));
assert.equal(context.activePointers.size,0);assert.deepEqual(selections,[]);
handlers.pointerdown(event(3,150,150));handlers.pointercancel(event(3,150,150));assert.deepEqual(selections,[]);
handlers.pointerdown(event(4,150,150));handlers.pointerup(event(4,150,150));assert.deepEqual(selections,['alpha']);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for campaign request ordering")
def test_war_selection_ignores_late_responses():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const pending = [];
const context = {
    state:{auth:{authenticated:true},geography:{sectors:[]}},
    warView:{requestSequence:0,planet:'first'},apiUrl:path=>path,
    fetch:()=>new Promise(resolve=>pending.push(resolve))
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('async function loadWar()'),html.indexOf('function warConsoleHtml()')),context);
(async()=>{
    const first=context.loadWar();
    context.warView.planet='second';
    const second=context.loadWar();
    pending[1]({ok:true,json:async()=>({planet:'second',campaigns:{}})});
    await second;
    pending[0]({ok:true,json:async()=>({planet:'first',campaigns:{}})});
    await first;
    assert.equal(context.warView.data.planet,'second');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for focused campaign polling")
def test_war_polling_continues_with_focus_and_preserves_drafts():
    source = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
let loads=0,focused=null;
const makeInput=()=>({value:'',selectionStart:0,selectionEnd:0,tagName:'INPUT',dataset:{},hasAttribute:name=>name==='data-war-reversal-reason',getAttribute:name=>name==='data-war-reversal-reason'?'':null,focus(){focused=this;},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}});
let input=makeInput();input.value='Verified duplicate credit';input.selectionStart=4;input.selectionEnd=8;focused=input;
const panel={querySelectorAll:()=>[input],set outerHTML(value){input=makeInput();}};
const context={state:{page:'reach',geography:{sectors:[]}},warView:{busy:false,data:{}},
 document:{visibilityState:'visible',get activeElement(){return focused;},querySelector:()=>null,querySelectorAll:()=>[],getElementById:identity=>identity==='war-console'?panel:{}},
 loadReach:async()=>{loads++;},warConsoleHtml:()=>'<section></section>',renderReach:()=>{throw Error('Canvas remounted');},updateStatusBadge:()=>{},titleCase:value=>value};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('function refreshWarConsole()'),html.indexOf('document.addEventListener("change", async event =>')),context);
vm.runInContext(html.slice(html.indexOf('async function refreshReach()'),html.indexOf('function visibleReachDirectives()')),context);
(async()=>{
 await context.refreshReach();
 assert.equal(loads,1);assert.equal(input.value,'Verified duplicate credit');
 assert.equal(focused,input);assert.equal(input.selectionStart,4);assert.equal(input.selectionEnd,8);
 focused={dataset:{warPool:'omega'},closest:()=>({})};
 await context.refreshReach();await context.refreshReach();assert.equal(loads,3);
 context.loadReach=async()=>{context.state.page='submit-aar';};
 context.refreshWarConsole=()=>{throw Error('Wrong page refreshed');};
 await context.refreshReach();
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for audited deletion handlers")
def test_forgemaster_direct_deletion_requires_confirmation_and_resumes_failures():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const requests=[];
const confirmations=[];
const status={textContent:'',focus:()=>{focuses++;},scrollIntoView:()=>{scrolls++;}};
let confirmed=false;
let failConfirmation=false;
let pending=false;
let refreshes=0;
let focuses=0,scrolls=0;
const locations=[];
const context={
    state:{page:'manage-aars',auth:{csrf:'csrf-token',aar_management:{can_delete:true}}},
    aarManager:{selectedId:'123',detail:{id:'123'}},apiUrl:path=>path,
    window:{confirm:message=>{confirmations.push(message);return confirmed;}},
    history:{replaceState:(_state,_title,path)=>locations.push(path)},
    document:{getElementById:()=>status},renderAarManagement:()=>{},
    loadAarManagement:async()=>{refreshes++;},
    fetch:async(url,options)=>{
        requests.push({url,...options});
        const request=url.endsWith('delete-request');
        return {ok:request || !failConfirmation,json:async()=>request
            ? {status:'requested',reason:'Duplicate report'}
            : failConfirmation ? {message:'Bridge unavailable'} : {status:pending?'processing_pending':'deleted'}};
    }
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('async function deleteManagedAar('),html.indexOf('function renderAarManagement()')),context);
(async()=>{
    await context.deleteManagedAar('123','Duplicate report',true);
    assert.equal(requests.length,0);
    confirmed=true;
    context.state.auth.aar_management.can_delete=false;
    await context.deleteManagedAar('123','Duplicate report',true);
    assert.equal(requests.length,0);
    context.state.auth.aar_management.can_delete=true;
    failConfirmation=true;
    await context.deleteManagedAar('123','Duplicate report',true);
    assert.equal(requests.length,2);
    assert(requests[0].url.endsWith('delete-request'));
    assert(requests[1].url.endsWith('delete-confirm'));
    assert.equal(JSON.parse(requests[0].body).reason,'Duplicate report');
    assert.equal(requests[0].headers['X-CSRF-Token'],'csrf-token');
    assert.equal(context.aarManager.detail.delete_request.reason,'Duplicate report');
    assert.equal(status.textContent,'Bridge unavailable');
    assert.equal(context.aarManager.deleteBusy,false);
    assert.match(confirmations.at(-1),/123[\s\S]*Duplicate report/);
    failConfirmation=false;pending=true;
    await context.deleteManagedAar('123','Duplicate report');
    assert.equal(requests.length,3);
    assert.equal(refreshes,1);
    assert.equal(context.aarManager.selectedId,'');
    assert.match(context.aarManager.notice,/still finishing/);
    assert.equal(focuses,1);assert.equal(scrolls,1);
    assert.equal(locations.at(-1),'/manage-aars');
    pending=false;
    context.aarManager.detail={id:'123',delete_request:{status:'requested'}};
    context.aarManager.selectedId='123';
    await context.deleteManagedAar('123','Duplicate report');
    assert.equal(context.aarManager.notice,'AAR 123 was deleted.');
    assert.equal(context.aarManager.selectedId,'');
    assert.equal(context.aarManager.reviewId,'');
    assert.equal(refreshes,2);assert.equal(focuses,2);assert.equal(scrolls,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the browser-handler regression")
def test_screenshot_sources_share_limits_and_submission_guard():
        source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
assert.match(html, /document\.addEventListener\("paste", event =>[\s\S]*?addAarScreenshots\(screenshots\)/);
const snippet = html.slice(html.indexOf('function addAarScreenshots('), html.indexOf('function aarAllowedTagKeys('));
const errors = [];
let previews = 0;
const context = {
    state: { auth: { aar_max_file_bytes: 16 * 1024 * 1024 } },
    aarDraft: { files: [], submitting: false, idempotencyKey: 'original', reviewing: true },
    AAR_MAX_SCREENSHOTS: 10, AAR_MAX_IMAGE_BYTES: 32 * 1024 * 1024, AAR_MAX_TOTAL_IMAGE_BYTES: 32 * 1024 * 1024,
    URL: { createObjectURL: () => { previews++; return 'blob:preview'; } },
    setAarStatus: message => errors.push(message), renderSubmitAar: () => {}
};
vm.createContext(context);
vm.runInContext(snippet, context);
const image = { name: 'proof.png', type: 'image/png', size: 1024 };
assert.equal(context.addAarScreenshots([image]), true);
assert.equal(context.aarDraft.files.length, 1);
assert.equal(context.aarDraft.idempotencyKey, '');
assert.equal(context.addAarScreenshots([{ ...image, type: 'image/svg+xml' }]), false);
assert.equal(context.addAarScreenshots([{ ...image, size: 16 * 1024 * 1024 }]), true);
assert.equal(context.addAarScreenshots([{ ...image, size: 16 * 1024 * 1024 + 1 }]), false);
assert.equal(context.addAarScreenshots(Array(10).fill(image)), false);
context.aarDraft.files = Array(2).fill({ ...image, size: 16 * 1024 * 1024 });
assert.equal(context.addAarScreenshots([image]), false);
context.aarDraft.files = [];
assert.equal(context.addAarScreenshots(Array(4).fill(image)), true);
assert.equal(context.aarDraft.files.length, 4);
assert.ok(context.aarDraft.files.every(file => file.previewUrl === 'blob:preview'));
context.aarDraft.files = [];
context.aarDraft.submitting = true;
assert.equal(context.addAarScreenshots([image]), false);
assert.equal(context.aarDraft.files.length, 0);
assert.equal(previews, 6);
assert.equal(errors.length, 4);
"""
        result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the AAR edit evidence regression")
def test_aar_edit_keeps_existing_screenshots_after_replacement_is_removed():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const snippet = html.slice(html.indexOf('function addAarScreenshots('), html.indexOf('function aarAllowedTagKeys('));
const existing = [{ filename: 'old.png', url: 'https://cdn.discordapp.com/old.png', size: 10 }];
const context = {
    state: { auth: { aar_max_file_bytes: 16 * 1024 * 1024 } },
    aarDraft: { files: [], submitting: false, idempotencyKey: 'k', reviewing: false, managementRecordId: '123', existingScreenshots: existing },
    AAR_MAX_SCREENSHOTS: 10, AAR_MAX_IMAGE_BYTES: 32 * 1024 * 1024, AAR_MAX_TOTAL_IMAGE_BYTES: 32 * 1024 * 1024,
    URL: { createObjectURL: () => 'blob:preview' }, setAarStatus: () => {}, renderSubmitAar: () => {}
};
vm.createContext(context);
vm.runInContext(snippet, context);
assert.equal(context.addAarScreenshots([{ name: 'new.png', type: 'image/png', size: 1024 }]), true);
context.aarDraft.files.splice(0, 1);
assert.deepEqual(context.aarDraft.existingScreenshots, existing);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the AAR navigation regression")
def test_aar_detail_back_navigation_is_available_before_detail_loads():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
assert.match(html, /aarManager.selectedId \? `<nav[^\n]*data-aar-manage-back[^\n]*Back to Manage AARs/);
assert.equal((html.match(/data-aar-manage-back>/g) || []).length, 1);
const navigation = html.indexOf('data-aar-manage-back>');
assert(navigation < html.indexOf('${preview}<div id="aar-management-status"'));
const start = html.indexOf('async function openAarManagementRecord(');
const end = html.indexOf('function renderAarManagement()', start);
let finishFetch;
const context = {
    aarManager: { selectedId: '', detail: null, status: 'ready', query: 'Inferno', offset: 25 },
    renderAarManagement: () => {}, apiUrl: path => path, history:{pushState:()=>{}},
    fetch: () => new Promise(resolve => { finishFetch = resolve; })
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
(async () => {
    const pending = context.openAarManagementRecord('123');
    assert.equal(context.aarManager.status, 'loading-detail');
    const handlerStart = html.indexOf('document.querySelector("[data-aar-manage-back]")');
    const handlerEnd = html.indexOf('document.querySelector("[data-aar-manage-edit]")', handlerStart);
    context.document = { querySelector: () => ({ addEventListener: (_event, callback) => callback() }) };
    vm.runInContext(html.slice(handlerStart, handlerEnd), context);
    finishFetch({ ok: true, json: async () => ({ id: '123' }) });
    await pending;
    assert.equal(context.aarManager.selectedId, '');
    assert.equal(context.aarManager.detail, null);
    assert.equal(context.aarManager.status, 'ready');
    assert.equal(context.aarManager.query, 'Inferno');
    assert.equal(context.aarManager.offset, 25);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for deletion review rendering")
def test_deletion_confirmation_is_clear_and_above_report_preview():
    source = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
const app={innerHTML:''};
const context={state:{auth:{aar_management:{can_edit:true,can_delete:true}}},
 aarManager:{detail:{id:'123',delete_request:{status:'requested',reason:'Duplicate report'},review:{kind:'delete',status:'requested'}},selectedId:'123',reviewId:'token',reviewType:'delete',view:'requests',status:'detail',records:[],editRequests:[],pendingDeletions:[],pendingEdits:[],reconciliationReviews:[],queueCounts:{},queueHistory:[]},
 document:{getElementById:id=>id==='app'?app:null,querySelector:()=>null,querySelectorAll:()=>[]},
 escapeHtml:value=>String(value??''),aarReviewHref:()=>'/manage-aars?aar=123',aarReportPreviewHtml:()=>'<h3>Mission evidence</h3>',aarDeletionDeclineHtml:()=>'<button>Decline deletion</button>',AAR_PVE_MISSIONS:[]};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('function aarSelectedReviewHtml('),html.indexOf('function updateStatusBadge()')),context);
context.renderAarManagement();
assert.match(app.innerHTML,/>Confirm delete<\/button>/);
assert(app.innerHTML.indexOf('data-aar-manage-delete-confirm')<app.innerHTML.indexOf('Mission evidence'));
assert.match(app.innerHTML,/Edits &amp; deletions/);
assert.doesNotMatch(app.innerHTML,/Processing issues|>Recovery</);
context.state.auth.aar_management.can_delete=false;
context.renderAarManagement();
assert.doesNotMatch(app.innerHTML,/data-aar-manage-delete-confirm/);
assert.match(app.innerHTML,/not authorized as Forgemaster/);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for award review member identity rendering")
def test_award_review_identifies_affected_member_in_queue_and_preview():
    source = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
const start=html.indexOf('function aarSelectedReviewHtml('),end=html.indexOf('function renderAarManagement()',start);
const context={escapeHtml:value=>String(value??''),AAR_SUBMISSION_MODES:{},AAR_SUBMISSION_TAGS:[],aarManager:{selectedId:'123',reviewId:'review-id'},aarReviewHref:(id,type,review)=>`/manage-aars?aar=${id}&review_type=${type}&review_id=${review}`,state:{auth:{aar_management:{can_delete:true}}}};
vm.createContext(context);vm.runInContext(html.slice(start,end),context);
const review={kind:'award',record_id:'123',review_id:'review-id',member_name:'Forge Master Jules',user_id:'42',challenge:'Black Laurels',status:'pending'};
const detail=context.aarSelectedReviewHtml({id:'123',review},'');
const row=context.aarReviewListItemHtml({id:'123',kind:'award',review:'review-id',label:'Award review',title:`${review.member_name} / ${review.challenge}`,text:'AAR 123'});
assert.match(detail,/Forge Master Jules/);assert.match(detail,/Black Laurels/);assert.match(detail,/Acknowledge this review/);
assert.match(row,/Forge Master Jules[\s\S]*Black Laurels/);assert.match(row,/review_type=award/);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for AAR history rendering")
def test_aar_history_cards_show_match_metadata_and_ticket_identity():
    source = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
const context={aarManager:{selectedId:'',reviewId:''},AAR_SUBMISSION_TAGS:[['black_laurels','Black Laurels']],
  AAR_SUBMISSION_MODES:{ops_strat:{label:'Operations / Stratagems'},pvp:{label:'PvP'}},
  escapeHtml:value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;'),
  aarReviewHref:(id,type,review)=>`/manage-aars?aar=${id}${type?'&review_type='+type+'&review_id='+review:''}`};
vm.createContext(context);
const start=html.indexOf('function aarReviewListItemHtml('),end=html.indexOf('function renderAarManagement()',start);
vm.runInContext(html.slice(start,end),context);
const history=context.aarReviewListItemHtml({kind:'',id:'123',review:'',label:'PvE',title:'Inferno',
  mode:'ops_strat',difficulty:'Ruthless',rank:'A',waves:10,kia:1,date:'2026-10-22T12:00:00Z',
  participants:['Brother One'],tags:['black_laurels']});
assert.match(history,/aar-history-item/);assert.match(history,/Ruthless/);assert.match(history,/Brother One/);
assert(history.includes('Operations / Stratagems'));assert.match(history,/Rank A/);
assert.match(history,/10 waves/);assert.match(history,/1 KIA/);
assert.match(history,/Black Laurels/);assert.match(history,/Open report/);
const zeroCounts=context.aarReviewListItemHtml({kind:'',id:'125',waves:0,kia:0});
assert.match(zeroCounts,/0 waves/);assert.match(zeroCounts,/0 KIA/);
const pvp=context.aarReviewListItemHtml({kind:'',id:'124',review:'',label:'PvP',title:'Match',
  mode:'pvp',difficulty:'@PvP Difficulty',pvp_result:'W'});
assert(pvp.includes('W'));
const ticket=context.aarReviewListItemHtml({kind:'delete',id:'123',review:'request-token',
  label:'Deletion request · Pending',title:'Inferno',text:'Duplicate report',date:'2026-10-22T12:00:00Z'});
assert.match(ticket,/aar-review-ticket/);assert.match(ticket,/Ticket · AAR 123/);
assert.match(ticket,/review_type=delete&amp;review_id=request-token|review_type=delete&review_id=request-token/);
const ordered=context.sortAarReviewTickets([
  {kind:'edit',date:'2026-10-22T12:00:00Z'},
  {kind:'delete',date:'2026-10-20T12:00:00Z'},
  {kind:'edit',date:'2026-10-21T12:00:00Z'}
]);
assert.deepEqual(ordered.map(item=>item.kind),['delete','edit','edit']);
const mappedRecords=context.mapAarHistoryRecords([{id:'321',mode:'ops_strat',mission:'Inferno',difficulty:'Ruthless',
  timestamp:'2026-10-22T12:00:00Z',brother_names:['Brother One'],brother_ids:['42'],
  tags:['black_laurels'],tag_labels:['Black Laurels'],rank:'A',waves:10,kia:1,pvp_result:null}]);
const mapped=context.aarReviewListItemHtml(mappedRecords[0]);
assert(mapped.includes('Operations / Stratagems'));assert.match(mapped,/Brother One/);
assert.match(mapped,/Black Laurels/);assert.match(mapped,/Rank A/);assert.match(mapped,/10 waves/);
assert.match(html,/Mission, participant, tag, or report field/);
assert.match(html,/normalized words across AAR IDs, receipt URLs/);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the acting-sergeant regression")
def test_kill_team_officers_are_shown_as_acting_sergeants():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const context = { state: { members: [], killTeams: {} } };
vm.createContext(context);
const ranksStart = html.indexOf('const KILL_TEAM_LEAD_RANKS =');
const ranksEnd = html.indexOf('const COMPANY_NAMES =', ranksStart);
vm.runInContext(html.slice(ranksStart, ranksEnd), context);
const sortingStart = html.indexOf('const RANK_TIERS =');
const sortingEnd = html.indexOf('const RANK_GUIDE =', sortingStart);
vm.runInContext(html.slice(sortingStart, sortingEnd), context);
const sortStart = html.indexOf('const RANK_TIER_INDEX =');
const sortEnd = html.indexOf('function formationEquerries(', sortStart);
assert(sortStart >= 0 && sortEnd > sortStart);
vm.runInContext(html.slice(sortStart, sortEnd), context);
context.RANK_LABELS = { watch_master: 'Watch Master', watch_captain: 'Watch Captain', watch_lieutenant: 'Watch Lieutenant', watch_sergeant: 'Watch Sergeant' };
context.displayName = name => name;
const helperStart = html.indexOf('function killTeamLead(');
const helperEnd = html.indexOf('function formationMembers(', helperStart);
vm.runInContext(html.slice(helperStart, helperEnd), context);
let commandPlate;
const app = { innerHTML: '' };
Object.assign(context, {
    document: { getElementById: () => app }, COMPANY_NAMES: { 1: 'Primus' },
    escapeHtml: value => String(value || ''),
    plateHtml: options => { commandPlate = options; return options.rankLabel; },
    renownSummaryHtml: () => '', unitHonoursHtml: () => '',
    commandSectionHtml: (_label, content) => content
});
const renderStart = html.indexOf('function renderKillTeam(');
vm.runInContext(html.slice(renderStart, html.indexOf('function chaptersOnRecord(', renderStart)), context);
for (const catalogued of [true, false]) {
    context.state.killTeams = catalogued ? { '1': [{ id: '1-1', name: 'Kill Team Test' }] } : {};
    for (const rank of ['watch_lieutenant', 'watch_captain', 'watch_master']) {
        const officer = { id: 'officer', name: 'Officer', rank, company: 1, killTeam: '1-1' };
        const brother = { id: 'brother', name: 'Brother', rank: 'watch_brother', company: 1, killTeam: '1-1' };
        context.state.members = [brother, officer, { ...officer, id: 'other', company: 2 }];
        assert.equal(context.killTeamsForCompany(1)[0].lead, officer);
        const detail = context.killTeamDetail(1, '1-1');
        assert.equal(detail.lead, officer);
        assert.equal(detail.rest.length, 1);
        assert.equal(detail.rest[0], brother);
        context.renderKillTeam(1, '1-1');
        assert.equal(commandPlate.member, officer);
        assert.equal(commandPlate.rankLabel, context.RANK_LABELS[rank]);
        assert.equal(commandPlate.dutyLabel, 'ACTING SERGEANT');
        assert.equal(context.killTeamLeadLabel(officer), context.RANK_LABELS[rank]);
        assert.equal(commandPlate.vacant, false);
        assert.equal(officer.rank, rank);
        const sergeant = { ...brother, id: 'sergeant', rank: 'watch_sergeant' };
        context.state.members.push(sergeant);
        assert.equal(context.killTeamDetail(1, '1-1').lead, sergeant);
        assert.equal(context.killTeamsForCompany(1)[0].lead, sergeant);
        context.renderKillTeam(1, '1-1');
        assert.equal(commandPlate.dutyLabel, '');
    }
}
context.state.members = [{ rank: 'watch_brother', company: 1, killTeam: '1-1' }];
assert.equal(context.killTeamDetail(1, '1-1').lead, null);
context.renderKillTeam(1, '1-1');
assert.equal(commandPlate.dutyLabel, '');
assert.equal(commandPlate.vacant, true);
context.serviceRecord = () => '';
const plateStart = html.indexOf('function plateHtml(');
vm.runInContext(html.slice(plateStart, html.indexOf('function renderBreadcrumb(', plateStart)), context);
const plate = context.plateHtml({ id: 'officer', name: 'Officer', rankLabel: 'Watch Captain', dutyLabel: 'ACTING SERGEANT' });
assert.match(plate, /class="plate-duty">ACTING SERGEANT/);
assert.match(plate, /class="plate-rank">Watch Captain/);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_unit_detail_headers_and_overview_symbols_share_scoped_layout():
    page = (server.ROOT / "jericho-strategium.html").read_text(encoding="utf-8")
    company = page.split("function renderCompany(", 1)[1].split("function renderFormation(", 1)[0]
    formation = page.split("function renderFormation(", 1)[1].split("function renderKillTeam(", 1)[0]
    for section, lore_class in ((company, "company-lore"), (formation, "formation-lore")):
        assert '<header class="unit-detail-header">' in section
        assert '<h2 class="tier-label unit-detail-title">' in section
        assert 'class="unit-detail-symbol"' in section
        assert section.index(f'class="{lore_class}"') < section.index('${renownSummaryHtml(')
    assert '<span class="company-plate-label"><span class="company-sigil"' in page
    assert 'style="--team-count:${teamList.length}"' in company


def test_company_titles_stay_on_one_line_beside_their_symbols():
    page = (server.ROOT / "jericho-strategium.html").read_text(encoding="utf-8")
    overview = page.split("function renderStrategium()", 1)[1].split("function drawMapConnectors()", 1)[0]
    assert '<span class="company-plate-name">Company ${COMPANY_NAMES[id]}</span>' in overview
    rule = re.search(r"\.company-plate-name\s*\{([^}]+)\}", page)
    assert rule is not None
    assert "min-width: 0" in rule.group(1)
    assert "white-space: nowrap" in rule.group(1)
    assert "letter-spacing: 0" in rule.group(1)
    assert "@media (min-width: 761px) and (max-width: 1024px)" in page
    assert ".map-company-row .company-slot { grid-column: auto; grid-row: auto; }" in page
    assert "if (!map || !svg || window.innerWidth <= 760) return;" in page


def test_header_emblem_column_accounts_for_all_three_title_lines():
    page = (server.ROOT / "jericho-strategium.html").read_text(encoding="utf-8")
    header = re.search(r"\.header-left\s*\{([^}]+)\}", page)
    title = re.search(r"\.header-title\s*\{([^}]+)\}", page)
    assert header is not None and title is not None
    assert "calc(var(--brand-font-size) * 3 * 81 / 96)" in header.group(1)
    assert "font-size: var(--brand-font-size)" in title.group(1)
    assert ".header-left { --brand-font-size: 17px; }" in page


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the guild member search regression")
def test_aar_member_search_queries_and_retains_guild_results():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function rerenderAarMemberSearch()');
const end = html.indexOf('function revokeAarPreview(', start);
assert(start >= 0 && end > start);
let timerCallback;
let requestedUrl = '';
let renderCount = 0;
const context = {
    aarDraft: {
        memberSearchTimer: null, memberSearchSequence: 0, memberSearchStatus: 'idle',
        memberSearchHasMore: false, memberDirectory: new Map()
    },
    state: { page: 'submit-aar' },
    document: { activeElement: null, getElementById: () => null },
    setTimeout: callback => { timerCallback = callback; return 1; },
    clearTimeout: () => {},
    apiUrl: path => path,
    fetch: async url => {
        requestedUrl = url;
        return { ok: true, json: async () => ({ members: [{ id: '99', name: 'Brother Reserve' }], has_more: true }) };
    },
    renderSubmitAar: () => { renderCount++; }
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
(async () => {
    context.scheduleAarGuildMemberSearch('Reserve Name');
    await timerCallback();
    assert.equal(requestedUrl, '/api/aar-members?q=Reserve%20Name');
    assert.deepEqual({ ...context.aarDraft.memberDirectory.get('99') }, { id: '99', name: 'Brother Reserve', fromGuildSearch: true });
    assert.equal(context.aarDraft.memberSearchStatus, 'results');
    assert.equal(context.aarDraft.memberSearchHasMore, true);
    assert.ok(renderCount >= 2);
    context.scheduleAarGuildMemberSearch('x');
    assert.equal(context.aarDraft.memberSearchStatus, 'idle');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the clipboard image import regression")
def test_clipboard_image_button_imports_supported_images():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('async function pasteAarImagesFromClipboard()');
const end = html.indexOf('function aarAllowedTagKeys(', start);
assert(start >= 0 && end > start);
assert.match(html, /id="aar-paste-clipboard"[^>]*>Paste from Clipboard/);
assert.match(html, /\.aar-clipboard-button \{ display: none; \}/);
const imported = [];
const statuses = [];
class MockFile {
    constructor(parts, name, options) {
        this.name = name;
        this.type = options.type;
        this.size = parts[0].size;
    }
}
const context = {
    navigator: { clipboard: { read: async () => [
        { types: ['text/plain', 'image/png', 'image/jpeg'], getType: async type => ({ type, size: 512 }) },
        { types: ['image/webp'], getType: async type => ({ type, size: 256 }) },
        { types: ['image/jpeg'], getType: async type => ({ type, size: 256 }) },
        { types: ['image/png'], getType: async type => ({ type, size: 256 }) }
    ] } },
    File: MockFile,
    aarDraft: { submitting: false },
    setAarStatus: (message, tone) => statuses.push([message, tone]),
    addAarScreenshots: files => { imported.push(...files); return true; }
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
(async () => {
    await context.pasteAarImagesFromClipboard();
    assert.deepEqual(imported.map(file => [file.name, file.type]), [
        ['clipboard-1.png', 'image/png'], ['clipboard-2.webp', 'image/webp'],
        ['clipboard-3.jpg', 'image/jpeg'], ['clipboard-4.png', 'image/png']
    ]);
    assert.deepEqual(statuses.at(-1), ['4 images added from clipboard.', 'success']);
    context.navigator.clipboard.read = async () => [{ types: ['text/plain'], getType: async () => ({ size: 5 }) }];
    await context.pasteAarImagesFromClipboard();
    assert.match(statuses.at(-1)[0], /No PNG, JPEG, or WebP image/);
    context.navigator.clipboard.read = async () => { throw new Error('NotAllowedError'); };
    await context.pasteAarImagesFromClipboard();
    assert.match(statuses.at(-1)[0], /denied or unavailable/);
    delete context.navigator.clipboard.read;
    await context.pasteAarImagesFromClipboard();
    assert.match(statuses.at(-1)[0], /unavailable here/);
    context.aarDraft.submitting = true;
    context.navigator.clipboard.read = async () => { throw new Error('must not read during submission'); };
    const statusCount = statuses.length;
    await context.pasteAarImagesFromClipboard();
    assert.equal(statuses.length, statusCount);
    assert.equal(imported.length, 4);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for clipboard paste regression")
def test_aar_paste_imports_all_files_without_duplicate_representations():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('  document.addEventListener("paste", event => {');
const end = html.indexOf('  for (const eventName of ["dragover", "drop"])', start);
const images = Array.from({ length: 4 }, () => ({ name: 'image.png', type: 'image/png', size: 512 }));
let handler;
let prevented = 0;
const imported = [];
let form = true;
const context = {
    state: { page: 'submit-aar' },
    document: { addEventListener: (_, callback) => { handler = callback; }, getElementById: () => form },
    addAarScreenshots: files => imported.push(...files)
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
const event = {
    clipboardData: { files: images, items: images.map(image => ({ kind: 'file', type: image.type, getAsFile: () => image })) },
    preventDefault: () => { prevented++; }
};
handler(event);
assert.equal(imported.length, 4);
assert.deepEqual(imported, images);
event.clipboardData.files = [];
handler(event);
assert.equal(imported.length, 8);
context.state.page = 'manage-aars';
handler(event);
assert.equal(imported.length, 12);
context.state.page = 'fortress';
handler(event);
context.state.page = 'submit-aar';
form = false;
handler(event);
form = true;
event.clipboardData.items = [{ kind: 'string', type: 'text/plain' }];
handler(event);
assert.equal(imported.length, 12);
assert.equal(prevented, 3);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for signed-in AAR labels")
def test_aar_signed_in_participant_keeps_guild_name_with_missing_or_stale_roster():
    source = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
const start=html.indexOf('  const userId = String(state.auth?.user?.id',html.indexOf('function renderSubmitAar()'));
const end=html.indexOf('  const memberSearchStatus =',start);
const context={state:{auth:{user:{id:'42',name:'Forge Master Jules'}},dataStatus:'live',members:[]},
 aarDraft:{brothers:new Set(['42']),inductees:new Set(),memberDirectory:new Map(),memberFilter:''},
 managementEdit:false,RANK_LABELS:{watch_brother:'Watch Brother',forgemaster:'Forge Master'},displayName:()=> 'Jules',escapeHtml:value=>String(value)};
vm.createContext(context);
vm.runInContext('function labels(){'+html.slice(start,end)+';return memberOptions;}',context);
for(const rank of [null,'watch_brother','forgemaster']){
 context.state.members=rank?[{id:'42',name:'Stale Jules',rank}]:[];
 const labels=context.labels();
 assert.match(labels,/>Forge Master Jules<\/span>/);
 assert.doesNotMatch(labels,/Watch Brother/);
}
context.state.members=[{id:'43',name:'Watch Brother Oldname',rank:'watch_brother'}];
context.aarDraft.memberDirectory.set('43',{id:'43',name:'Forge Master Newname',fromGuildSearch:true});
for(const query of ['newname','forge master']){
 context.aarDraft.memberFilter=query;
 const labels=context.labels();
 assert.match(labels,/value="43"/);
 assert.match(labels,/Forge Master Newname/);
 assert.doesNotMatch(labels,/Oldname/);
}
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for AAR editor hydration regression")
def test_aar_management_edit_hydrates_existing_submission():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function beginAarManagementEdit(');
const end = html.indexOf('async function loadAarManagement(', start);
assert(start >= 0 && end > start);
let rendered = 0;
let revoked = 0;
const context = {
    aarDraft: {
        files: [{ previewUrl: 'blob:old' }], memberDirectory: new Map(), brothers: new Set(),
        inductees: new Set(), tags: new Set()
    },
    AAR_SUBMISSION_MODES: { induction_omega: { difficulty: '@Omega' } },
    revokeAarPreview: () => { revoked++; },
    renderSubmitAar: () => { rendered++; }
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
context.beginAarManagementEdit({
    id: '123',
    submission: {
        mode: 'induction_omega', mission: 'pve_inferno', difficulty: '@Omega-Strat', rank: 'A',
        armory_data: 2, waves: 10, kia: 1, gene_seed_status: 'carried', gene_seed_carrier_id: '102',
        brother_ids: ['101', '102'], inductee_ids: ['102'], tags: ['black_laurels'],
        pvp_map: 'Bridge', pvp_game_mode: 'Seize Ground', pvp_result: 'L'
    },
    brothers: [{ id: '101', name: 'Brother One' }, { id: '102', name: 'Brother Two' }],
    screenshots: [{ filename: 'proof.png', url: 'https://cdn.discordapp.com/proof.png', size: 100 }]
});
assert.equal(context.aarDraft.managementRecordId, '123');
assert.equal(context.aarDraft.mode, 'induction_omega');
assert.equal(context.aarDraft.mission, 'pve_inferno');
assert.equal(context.aarDraft.difficulty, '@Omega-Strat');
assert.deepEqual([...context.aarDraft.brothers], ['101', '102']);
assert.deepEqual([...context.aarDraft.inductees], ['102']);
assert.deepEqual([...context.aarDraft.tags], ['black_laurels']);
assert.equal(context.aarDraft.memberDirectory.get('102').name, 'Brother Two');
assert.equal(context.aarDraft.geneCarrier, '102');
assert.equal(context.aarDraft.existingScreenshots.length, 1);
context.beginAarManagementEdit({id:'456',original_record_hash:'a'.repeat(64),submission:{mode:'induction_omega',brother_ids:['101','102']}},true);
assert.equal(context.aarDraft.memberProposal,true);
assert.equal(context.aarDraft.originalRecordHash,'a'.repeat(64));
assert.equal(revoked, 1);
assert.equal(rendered, 2);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the AAR tag eligibility regression")
def test_herisor_tag_matches_challenge_eligibility():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('const AAR_PVE_MISSIONS = [');
const end = html.indexOf('function renderSubmitAar()', start);
assert(start >= 0 && end > start);
const context = { escapeHtml: value => String(value) };
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
const allowed = (mission, difficulty, brothers) =>
    Array.from(context.aarAllowedTagKeys('ops_strat', mission, difficulty, brothers, []));

assert.ok(allowed('pve_termination', '@Hard-Siege', 3).includes('herisor_defense'));
assert.ok(!allowed('pve_termination', '@Normal-Siege', 3).includes('herisor_defense'));
assert.ok(allowed('pve_termination', '@Hard-Stratagem', 3).includes('herisor_defense'));
assert.ok(!allowed('pve_termination', '@Hard-Stratagem', 2).includes('herisor_defense'));
const disabledTags = context.aarTagOptionsHtml(false, allowed('pve_termination', '@Normal-Siege', 3), []);
assert.match(disabledTags, /value="herisor_defense" disabled/);
assert.match(disabledTags, /Defense of Herisor/);
const enabledTags = context.aarTagOptionsHtml(false, allowed('pve_termination', '@Hard-Siege', 3), []);
assert.match(enabledTags, /value="herisor_defense"/);
assert.doesNotMatch(enabledTags, /value="herisor_defense" disabled/);
assert.equal(context.aarTagOptionsHtml(true, [], []), '');
"""
    subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the AAR roster interaction regression")
def test_aar_member_selection_clears_name_search():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('form.querySelectorAll("[data-aar-brother]")');
const end = html.indexOf('form.querySelectorAll("[data-aar-inductee]")', start);
assert(start >= 0 && end > start);
let changeHandler;
let renders = 0;
let focused = false;
const searches = [];
const form = { querySelectorAll: () => [{ addEventListener: (name, handler) => {
    assert.equal(name, 'change');
    changeHandler = handler;
} }] };
const context = {
    form,
    aarDraft: { memberFilter: 'watch brother' },
    invalidateIdempotencyKey: () => {},
    readDraftFields: () => {},
    scheduleAarGuildMemberSearch: query => searches.push(query),
    renderSubmitAar: () => { renders++; },
    document: { getElementById: () => ({ focus: () => { focused = true; } }) }
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
assert.equal(typeof changeHandler, 'function');
changeHandler();
assert.equal(context.aarDraft.memberFilter, '');
assert.deepEqual(searches, ['']);
assert.equal(renders, 1);
assert.equal(focused, true);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the formation display regression")
def test_honored_dreadnought_equerry_is_not_displayed_on_site():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const definitions = html.slice(html.indexOf('const RANK_LABELS = {'), html.indexOf('const ATLAS_LOCATIONS = ['));
const selector = html.slice(html.indexOf('function formationEquerries(key) {'), html.indexOf('const EQUERRY_TITLES = {'));
const honored = { id: 'honored', rank: 'honored_dreadnought', formation: 'armory', equerry: true };
const techmarine = { id: 'specialist', rank: 'techmarine', formation: 'armory', equerry: true };
const context = { state: { members: [honored, techmarine] }, sortByRank: members => members };
vm.createContext(context);
vm.runInContext(definitions, context);
vm.runInContext(selector, context);

assert.deepEqual(context.formationEquerries('armory').map(member => member.id), ['specialist']);
assert.equal(vm.runInContext('FORMATIONS.armory.ranks.includes("honored_dreadnought")', context), true);
assert.equal(vm.runInContext('RANK_LABELS.honored_dreadnought', context), 'Honored Dreadnought');
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the personnel display regression")
def test_oathsworn_common_path_and_oath_seal_placeholder():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const tracks = html.slice(html.indexOf('const RANK_GUIDE_TRACKS = ['), html.indexOf('const RANK_GUIDE_TRACK_GROUPS = ['));
const seal = html.slice(html.indexOf('function oathSealHtml(member) {'), html.indexOf('const RENOWN_TIERS = ['));
const context = {};
vm.createContext(context);
vm.runInContext(tracks + '\nglobalThis.tracks = RANK_GUIDE_TRACKS;', context);
vm.runInContext(seal + '\nglobalThis.seal = oathSealHtml({ oathSeal: null });', context);
const common = context.tracks.find(track => track.id === 'common');
const command = context.tracks.find(track => track.id === 'command');
assert.deepEqual(Array.from(common.ranks), [1, 2, 3]);
assert.deepEqual(Array.from(command.ranks), [4, 5, 6, 7, 27]);
assert.match(context.seal, /Oath Seal/);
assert.match(context.seal, /Awaiting assignment/);
assert.match(html, /\$\{oathSealHtml\(m\)\}/);
"""
    subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the map-focus regression")
def test_sector_and_system_focus_center_in_uncovered_map_area():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function geographyPanelFocusWidth()');
const end = html.indexOf('function showReachLevel()', start);
assert(start >= 0 && end > start);
const state = {
    selectedSector: 'sector_02', selectedSystem: null,
    geography: {
        sectors: [
            { id: 'sector_02', number: 2, boundary: [[0,0],[1000,0],[1000,400],[0,400]] },
            { id: 'sector_28', number: 28, boundary: [[0,0],[585,0],[585,1555],[0,1555]] }
        ],
        systems: [{ id: 'system_01', sectorId: 'sector_02', x: 100, y: 150 }],
        bodies: [
            { id: 'star', systemId: 'system_01', kind: 'star', orbitIndex: 0, parentBodyId: null },
            { id: 'world_05', systemId: 'system_01', kind: 'planet', orbitIndex: 5, parentBodyId: null }
        ]
    },
    reachCanvasCamera: { baseZoom: .05, zoom: .05, targetZoom: .05, x: 500, y: 200, targetX: 500, targetY: 200 }
};
const context = {
    state,
    SECTOR_FOCUS_MAX_ZOOM_RATIO: 3.5,
    getComputedStyle: () => ({position: context.panelPosition}), panelPosition:'absolute',
    document: { getElementById: identity => ({ getBoundingClientRect: () => ({ width: identity === 'reach-sector-panel' ? 340 : 1000, height: 500 }) }) }
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
assert.equal(context.focusGeographySectorCamera('sector_02'), true);
let camera = state.reachCanvasCamera;
let centerScreen = (500 - camera.targetX) * camera.targetZoom + 500;
assert.ok(Math.abs(centerScreen - 330) < 1e-9);
assert.ok(camera.targetZoom >= camera.baseZoom);
assert.ok(camera.targetZoom <= camera.baseZoom * 120);
const sector02Zoom = camera.targetZoom;
assert.equal(context.focusGeographySectorCamera('sector_28'), true);
camera = state.reachCanvasCamera;
assert.ok(Math.abs(camera.targetZoom - sector02Zoom) < 1e-12);
centerScreen = (292.5 - camera.targetX) * camera.targetZoom + 500;
assert.ok(Math.abs(centerScreen - 330) < 1e-9);
assert.equal(context.focusGeographySystemCamera('system_01'), true);
camera = state.reachCanvasCamera;
centerScreen = (100 - camera.targetX) * camera.targetZoom + 500;
assert.ok(Math.abs(centerScreen - 330) < 1e-9);
assert.ok(camera.targetZoom <= camera.baseZoom * 120);
context.panelPosition='static';
assert.equal(context.geographyPanelFocusWidth(),0);
context.focusGeographySectorCamera('sector_02');
camera=state.reachCanvasCamera;
assert.ok(Math.abs((500-camera.targetX)*camera.targetZoom+500-500)<1e-9);
context.focusGeographySystemCamera('system_01');
assert.equal(camera.targetX,100);
"""
    result = subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the map-renderer regression")
def test_shared_map_background_tracks_camera_and_updates_every_frame():
    source = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('  function drawBackground(');
const end = html.indexOf('  function drawArt(', start);
assert(start >= 0 && end > start);
const calls = {};
let draws = 0;
const gl = { isContextLost: () => false, useProgram: () => {}, TRIANGLE_STRIP: 5 };
for (const name of ['viewport', 'uniform2f', 'uniform4f', 'uniform1f']) {
    gl[name] = (...values) => { calls[values[0]] = values.slice(1); };
}
gl.drawArrays = () => { draws++; };
const names = ['u_view', 'u_artRect', 'u_starRect', 'u_alpha', 'u_time', 'u_motion'];
const renderer = { gl, program: {}, uniforms: Object.fromEntries(names.map(name => [name, name])) };
const context = {
    backgroundUnavailable: false, backgroundMotionStart: null,
    backgroundCanvas: { width: 0, height: 0 },
    width: 1200, height: 675, deviceScale: 2,
    REACH_ART: { x: 0, y: 0, width: 1672, height: 941, scale: 5 },
    reducedMotion: { matches: false },
    MAP_MOTION_START_RATIO: 1.55, MAP_MOTION_END_RATIO: 2.1,
    reachArtImage: () => ({ naturalWidth: 1800, naturalHeight: 1000 }),
    backgroundRendererFor: () => renderer,
    worldToScreen: (x, y, camera) => ({
        x: (x - camera.x) * camera.zoom + 600,
        y: (y - camera.y) * camera.zoom + 337.5
    }),
    canvasSmoothstep: (start, end, value) => {
        const progress = Math.max(0, Math.min(1, (value - start) / (end - start)));
        return progress * progress * (3 - 2 * progress);
    }
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
const camera = { x: 4000, y: 2000, zoom: .2 };
assert.equal(context.drawBackground(camera, 1, 1000), true);
assert.deepEqual(calls.u_view, [1200, 675]);
assert.deepEqual(calls.u_artRect, [-200, -62.5, 1672, 941]);
assert.deepEqual(calls.u_starRect, [-300, -162.5, 1800, 1000]);
assert.equal(context.backgroundCanvas.width, 2400);
assert.equal(context.backgroundCanvas.height, 1350);
assert.deepEqual(calls.u_motion, [1]);
assert.equal(context.drawBackground(camera, 1, 1016), true);
assert.equal(draws, 2);
assert.deepEqual(calls.u_time, [.016]);
camera.zoom = .4;
context.drawBackground(camera, 3, 1032);
assert.deepEqual(calls.u_artRect, [-1000, -462.5, 3344, 1882]);
assert.deepEqual(calls.u_motion, [0]);
context.reducedMotion.matches = true;
context.drawBackground(camera, 1, 1048);
assert.deepEqual(calls.u_motion, [0]);
assert.deepEqual(calls.u_time, [0]);
context.backgroundUnavailable = true;
assert.equal(context.drawBackground(camera, 1, 1064), false);
assert.equal(draws, 4);
"""
    subprocess.run(["node", "-e", source, str(server.ROOT / "jericho-strategium.html")], check=True, capture_output=True, text=True)
