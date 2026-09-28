import time
import threading
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
    _origin_matches,
    _request_is_secure,
    _ribbon_path,
    _pauldron_path,
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


def test_record_of_blood_direct_route(local_site) -> None:
    for method in ("GET", "HEAD"):
        with urllib.request.urlopen(urllib.request.Request(local_site + "/record-of-blood", method=method)) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert (b"Record of Blood" in response.read()) is (method == "GET")


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
    for name in ("../server.py", "test.svg", "Blood Angels.png/extra", "missing.png", "%2e%2e%2fserver.py"):
        assert _pauldron_path(urllib.parse.unquote(name)) is None
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(local_site + "/assets/pauldrons/" + name.replace(" ", "%20"))
        assert error.value.code == 404


def test_ambience_is_optional_and_served_from_fixed_path(local_site, tmp_path, monkeypatch) -> None:
    track = tmp_path / "fortress-ambience.mp3"
    monkeypatch.setattr(server, "AMBIENCE_PATH", track)
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
