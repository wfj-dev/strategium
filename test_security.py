import time

from server import (
    BACKSTORY_MAX_CHARS,
    BACKSTORY_MAX_WORDS,
    _backstory_error,
    _cookie_flags,
    _origin_matches,
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
