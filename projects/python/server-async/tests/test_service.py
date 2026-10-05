from typing import Any

import pytest

from text_service.service import Service


def test_account_lifecycle() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("GET", "/ping", None, "") == (200, {"data": "pong"})
    assert service.handle("POST", "/users", account, "")[0] == 201
    assert service.handle("POST", "/users", account, "")[0] == 409
    assert service.handle("POST", "/sessions", {**account, "password": "incorrect"}, "")[0] == 401
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    next_token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    assert token != next_token
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 401
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}") == (200, {"data": []})
    assert service.handle("DELETE", "/sessions/current", None, f"Bearer {next_token}")[0] == 200
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}")[0] == 401


def test_validation() -> None:
    service = Service()
    for body in (
        None,
        [],
        {},
        {"username": True, "password": "password1"},
        {"username": "a/b", "password": "password1"},
    ):
        assert service.handle("POST", "/users", body, "")[0] == 400


def test_concurrent_registration() -> None:
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    body = {"username": "alice", "password": "password1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: service.handle("POST", "/users", body, "")[0], range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]


def test_echo() -> None:
    service = Service()
    body = {"text": "Hello, World!"}
    assert service.handle("POST", "/echo", body, "") == (200, {"data": "Hello, World!"})
    assert service.handle("POST", "/echo", {"text": "你好\nRM"}, "") == (200, {"data": "你好\nRM"})
    assert service.handle("POST", "/echo", {"text": ""}, "") == (200, {"data": ""})
    for bad in (None, [], {}, {"text": 42}, {"text": "a", "extra": 1}, {"text": "\ud800"}):
        assert service.handle("POST", "/echo", bad, "")[0] == 400
    assert service.handle("POST", "/echo", {"text": "x" * 65536}, "")[0] == 200
    assert service.handle("POST", "/echo", {"text": "x" * 65537}, "")[0] == 413
    # 65,536 bytes measured after UTF-8 encoding, not by character count.
    assert service.handle("POST", "/echo", {"text": "你" * 21845}, "")[0] == 200
    assert service.handle("POST", "/echo", {"text": "你" * 21846}, "")[0] == 413


def _token(service: Service, username: str) -> str:
    service.handle("POST", "/users", {"username": username, "password": "password1"}, "")
    _, result = service.handle(
        "POST", "/sessions", {"username": username, "password": "password1"}, ""
    )
    return f"Bearer {result['data']['token']}"


def test_texts() -> None:
    service = Service()
    auth = _token(service, "alice")
    assert service.handle("GET", "/texts/note", None, auth)[0] == 404
    assert service.handle("PUT", "/texts/note", {"text": "test\nRM"}, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": "test\nRM"})
    assert service.handle("GET", "/texts", None, auth) == (200, {"data": ["note"]})
    # Empty text is stored and read back as 200, not mistaken for a missing text.
    assert service.handle("PUT", "/texts/note", {"text": ""}, auth)[0] == 200
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": ""})
    # Name validation.
    for bad_path in ("/texts/bad name", "/texts/bad.name", f"/texts/{'a' * 65}"):
        assert service.handle("GET", bad_path, None, auth)[0] == 400
    # Field validation and limits.
    for bad in (None, [], {}, {"text": 42}, {"text": "a", "extra": 1}):
        assert service.handle("PUT", "/texts/note", bad, auth)[0] == 400
    assert service.handle("PUT", "/texts/note", {"text": "x" * 65537}, auth)[0] == 413
    # Authentication is checked before existence.
    assert service.handle("PUT", "/texts/note", {"text": "x"}, "")[0] == 401
    assert service.handle("GET", "/texts/note", None, "Bearer nope")[0] == 401
    # Users are isolated: another user sees no "note".
    bob = _token(service, "bob")
    assert service.handle("GET", "/texts/note", None, bob)[0] == 404
    # DELETE removes the text and is idempotent per user.
    assert service.handle("DELETE", "/texts/note", None, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts/note", None, auth)[0] == 404
    assert service.handle("GET", "/texts", None, auth) == (200, {"data": []})
    assert service.handle("DELETE", "/texts/note", None, auth)[0] == 404
    assert service.handle("DELETE", "/texts/note", None, "")[0] == 401


def test_delete_user() -> None:
    service = Service()
    auth = _token(service, "alice")
    assert service.handle("PUT", "/texts/note", {"text": "bye"}, auth)[0] == 200
    # Invalid identity is rejected.
    assert service.handle("DELETE", "/users/me", None, "")[0] == 401
    assert service.handle("DELETE", "/users/me", None, "Bearer nope")[0] == 401
    assert service.handle("DELETE", "/users/me", None, auth) == (200, {"data": None})
    # The revoked token can no longer read or rewrite texts.
    assert service.handle("GET", "/texts", None, auth)[0] == 401
    assert service.handle("PUT", "/texts/note", {"text": "x"}, auth)[0] == 401
    # Re-registering the same name starts clean with a fresh token.
    new_auth = _token(service, "alice")
    assert service.handle("GET", "/texts", None, new_auth) == (200, {"data": []})
    assert service.handle("GET", "/texts/note", None, new_auth)[0] == 404
    assert service.handle("GET", "/texts", None, auth)[0] == 401


def test_login_does_not_apply_to_reregistered_account(monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import threading
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    account = {"username": "alice", "password": "password1"}
    old_auth = _token(service, "alice")

    original = hashlib.pbkdf2_hmac
    armed = threading.Event()
    release = threading.Event()
    block_next = False

    def gated_pbkdf2(*args: Any, **kwargs: Any) -> bytes:
        nonlocal block_next
        if block_next:
            block_next = False
            armed.set()
            assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(hashlib, "pbkdf2_hmac", gated_pbkdf2)
    block_next = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        # This login reads the old account, then blocks inside the password hash.
        future = pool.submit(service.handle, "POST", "/sessions", account, "")
        assert armed.wait(timeout=5)
        # While the old login is in flight, delete the account and re-register the same name.
        assert service.handle("DELETE", "/users/me", None, old_auth)[0] == 200
        new_auth = _token(service, "alice")
        # Release the old login: it must not bind a token to the new account.
        release.set()
        assert future.result(timeout=5)[0] == 401
    assert service.handle("GET", "/texts", None, new_auth) == (200, {"data": []})


class FakeClock:
    """Deterministic clock so expiry can be tested without sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_login_reports_ttl_and_operations_do_not_renew() -> None:
    clock = FakeClock()
    service = Service(token_ttl_seconds=120, clock=clock)
    service.handle("POST", "/users", {"username": "alice", "password": "password1"}, "")
    _, result = service.handle(
        "POST", "/sessions", {"username": "alice", "password": "password1"}, ""
    )
    assert result["data"]["expires_in"] == 120
    auth = f"Bearer {result['data']['token']}"

    clock.now = 60.0
    # Protected operations succeed but must not extend the deadline.
    assert service.handle("GET", "/texts", None, auth)[0] == 200
    assert service.handle("GET", "/texts", None, auth)[0] == 200
    # Reaching the deadline (now == ttl) invalidates the token.
    clock.now = 120.0
    assert service.handle("GET", "/texts", None, auth)[0] == 401


def test_expired_token_is_rejected_on_every_protected_route() -> None:
    clock = FakeClock()
    service = Service(token_ttl_seconds=300, clock=clock)
    auth = _token(service, "alice")
    clock.now = 300.0
    cases = (
        ("GET", "/texts", None),
        ("PUT", "/texts/note", {"text": "x"}),
        ("GET", "/texts/note", None),
        ("DELETE", "/texts/note", None),
        ("DELETE", "/sessions/current", None),
        ("DELETE", "/users/me", None),
    )
    for method, path, body in cases:
        assert service.handle(method, path, body, auth)[0] == 401


def test_relogin_replaces_token() -> None:
    clock = FakeClock()
    service = Service(token_ttl_seconds=100, clock=clock)
    old_auth = _token(service, "alice")
    clock.now = 100.0
    assert service.handle("GET", "/texts", None, old_auth)[0] == 401

    new_auth = _token(service, "alice")
    assert new_auth != old_auth
    assert service.handle("GET", "/texts", None, new_auth) == (200, {"data": []})
    assert service.handle("GET", "/texts", None, old_auth)[0] == 401


def test_logout_and_delete_user_revoke_before_expiry() -> None:
    clock = FakeClock()
    service = Service(token_ttl_seconds=300, clock=clock)

    logout_auth = _token(service, "alice")
    clock.now = 1.0
    assert service.handle("DELETE", "/sessions/current", None, logout_auth) == (200, {"data": None})
    assert service.handle("GET", "/texts", None, logout_auth)[0] == 401

    delete_auth = _token(service, "bob")
    clock.now = 2.0
    assert service.handle("DELETE", "/users/me", None, delete_auth) == (200, {"data": None})
    assert service.handle("GET", "/texts", None, delete_auth)[0] == 401


def test_concurrent_text_writes_and_account_deletion() -> None:
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    auth = _token(service, "alice")
    with ThreadPoolExecutor(max_workers=8) as pool:
        writes = [
            pool.submit(service.handle, "PUT", f"/texts/note{i}", {"text": str(i)}, auth)
            for i in range(32)
        ]
        removal = pool.submit(service.handle, "DELETE", "/users/me", None, auth)
        statuses = [future.result()[0] for future in (*writes, removal)]
    # A write either lands (200) or is rejected as unauthenticated (401); never a crash.
    assert set(statuses) <= {200, 401}
    # Deletion is the only token-invalidating operation, so it always succeeds.
    assert statuses[-1] == 200
    # Deletion wins permanently: no concurrent write can resurrect the account.
    assert "alice" not in service.users
    assert service.handle("GET", "/texts", None, auth) == (401, {"message": "Login required"})


def test_concurrent_text_writes_and_login_replacement() -> None:
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    account = {"username": "alice", "password": "password1"}
    old_auth = _token(service, "alice")
    with ThreadPoolExecutor(max_workers=8) as pool:
        writes = [
            pool.submit(service.handle, "PUT", f"/texts/note{i}", {"text": str(i)}, old_auth)
            for i in range(32)
        ]
        login = pool.submit(service.handle, "POST", "/sessions", account, "")
        statuses = [future.result()[0] for future in writes]
        new_token = login.result()[1]["data"]["token"]
    assert set(statuses) <= {200, 401}
    # The replacement revokes the old token while new writes remain readable and consistent.
    assert service.handle("GET", "/texts", None, old_auth)[0] == 401
    _, listing = service.handle("GET", "/texts", None, f"Bearer {new_token}")
    names = listing["data"]
    assert names == sorted(set(names))
    for text_name_value in names:
        assert (
            service.handle("GET", f"/texts/{text_name_value}", None, f"Bearer {new_token}")[0]
            == 200
        )
