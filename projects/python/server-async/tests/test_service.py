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
