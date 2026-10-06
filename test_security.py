import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server
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


def test_origin_matches_exact_allowed_origin() -> None:
    assert _origin_matches("http://127.0.0.1:8787", "http://127.0.0.1:8787")
    assert _origin_matches("http://127.0.0.1:8787/", "http://127.0.0.1:8787")
    assert not _origin_matches("https://evil.example", "http://127.0.0.1:8787")


def test_cookie_flags_include_secure_when_required() -> None:
    flags = _cookie_flags(secure=True)
    assert "; Secure" in flags
    assert "SameSite=Lax" in flags


def test_request_is_secure_uses_forwarded_proto() -> None:
    headers = {"X-Forwarded-Proto": "https"}
    assert _request_is_secure(headers)
    assert not _request_is_secure({"X-Forwarded-Proto": "http"})


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


@pytest.mark.parametrize("filename", ["base.webp", "sectors.png", "1-secure.webp", "29-critical.webp", "4-lost.webp"])
def test_galactic_map_assets_are_allowlisted(local_site, filename) -> None:
    with urllib.request.urlopen(local_site + "/assets/galactic-map/" + filename) as response:
        assert response.status == 200
        assert response.read()


@pytest.mark.parametrize("filename", ["0-secure.webp", "30-secure.webp", "1-contested.webp", "geometry.json", "%2e%2e/server.py", "1-secure.png"])
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
    (tmp_path / "1-Strategium.webp").write_bytes(b"layer")
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
