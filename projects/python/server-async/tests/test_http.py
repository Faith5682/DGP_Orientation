import asyncio
import threading
from collections.abc import AsyncGenerator

import pytest
from httpx2 import ASGITransport, AsyncClient

from text_service.server import create_app
from text_service.service import Service

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def test_http_routes(client: AsyncClient) -> None:
    assert (await client.get("/ping")).status_code == 200
    response = await client.post("/users", json={"username": "alice", "password": "password1"})
    assert response.status_code == 201
    response = await client.post("/sessions", json={"username": "alice", "password": "password1"})
    token = response.json()["data"]["token"]
    assert (
        await client.get("/texts", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (await client.get("/texts")).status_code == 401
    assert (
        await client.post(
            "/users", content=b"not JSON", headers={"Content-Type": "application/json"}
        )
    ).status_code == 400
    assert (
        await client.post(
            "/users", content=b"x" * 524289, headers={"Content-Type": "application/json"}
        )
    ).status_code == 413


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
async def test_invalid_json(client: AsyncClient, body: bytes) -> None:
    assert (await client.post("/users", content=body)).status_code == 400


async def test_echo_roundtrip(client: AsyncClient) -> None:
    assert (await client.post("/echo", json={"text": "你好\nRM"})).json() == {"data": "你好\nRM"}
    assert (await client.post("/echo", json={"text": ""})).json() == {"data": ""}


@pytest.mark.parametrize("body", [[], {}, {"text": 42}, {"text": "a", "extra": 1}])
async def test_echo_rejects_bad_fields(client: AsyncClient, body: object) -> None:
    assert (await client.post("/echo", json=body)).status_code == 400


async def test_echo_rejects_unpaired_surrogate(client: AsyncClient) -> None:
    response = await client.post(
        "/echo", content=b'{"text":"\\ud800"}', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


async def test_echo_text_limit(client: AsyncClient) -> None:
    assert (await client.post("/echo", json={"text": "x" * 65536})).status_code == 200
    assert (await client.post("/echo", json={"text": "x" * 65537})).status_code == 413


async def auth_headers(client: AsyncClient, username: str = "alice") -> dict[str, str]:
    await client.post("/users", json={"username": username, "password": "password1"})
    response = await client.post("/sessions", json={"username": username, "password": "password1"})
    token = response.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


async def test_text_upload_and_read(client: AsyncClient) -> None:
    headers = await auth_headers(client)
    assert (await client.get("/texts/note", headers=headers)).status_code == 404
    assert (await client.put("/texts/note", json={"text": "你好\nRM"}, headers=headers)).json() == {
        "data": None
    }
    assert (await client.get("/texts/note", headers=headers)).json() == {"data": "你好\nRM"}
    assert (await client.get("/texts", headers=headers)).json() == {"data": ["note"]}
    assert (await client.put("/texts/note", json={"text": ""}, headers=headers)).status_code == 200
    assert (await client.get("/texts/note", headers=headers)).json() == {"data": ""}


async def test_text_requires_authentication(client: AsyncClient) -> None:
    assert (await client.put("/texts/note", json={"text": "x"})).status_code == 401
    assert (await client.get("/texts/note")).status_code == 401
    assert (
        await client.get("/texts/note", headers={"Authorization": "Bearer nope"})
    ).status_code == 401


@pytest.mark.parametrize("name", ["bad name", "bad.name", "a" * 65])
async def test_text_rejects_bad_name(client: AsyncClient, name: str) -> None:
    headers = await auth_headers(client)
    assert (await client.get(f"/texts/{name}", headers=headers)).status_code == 400


@pytest.mark.parametrize("body", [[], {}, {"text": 42}, {"text": "a", "extra": 1}])
async def test_text_put_rejects_bad_fields(client: AsyncClient, body: object) -> None:
    headers = await auth_headers(client)
    assert (await client.put("/texts/note", json=body, headers=headers)).status_code == 400


async def test_text_put_limits(client: AsyncClient) -> None:
    headers = await auth_headers(client)
    assert (
        await client.put("/texts/note", json={"text": "x" * 65536}, headers=headers)
    ).status_code == 200
    assert (
        await client.put("/texts/note", json={"text": "x" * 65537}, headers=headers)
    ).status_code == 413


async def test_texts_are_per_user(client: AsyncClient) -> None:
    alice = await auth_headers(client, "alice")
    bob = await auth_headers(client, "bob")
    await client.put("/texts/note", json={"text": "alice-text"}, headers=alice)
    await client.put("/texts/note", json={"text": "bob-text"}, headers=bob)
    assert (await client.get("/texts/note", headers=alice)).json() == {"data": "alice-text"}
    assert (await client.get("/texts/note", headers=bob)).json() == {"data": "bob-text"}


async def test_body_limit_and_routing(client: AsyncClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert (await client.post("/users", content=exact)).status_code == 400
    assert (await client.post("/users", content=exact + b" ")).status_code == 413
    assert (await client.get("/missing")).status_code == 404
    assert (await client.get("/echo")).status_code == 405
    assert (await client.patch("/ping")).status_code == 405
    assert (await client.get("/ping?test=1")).json() == {"data": "pong"}


async def test_text_delete(client: AsyncClient) -> None:
    headers = await auth_headers(client)
    await client.put("/texts/note", json={"text": "bye"}, headers=headers)
    assert (await client.delete("/texts/note", headers=headers)).json() == {"data": None}
    assert (await client.get("/texts/note", headers=headers)).status_code == 404
    assert (await client.get("/texts", headers=headers)).json() == {"data": []}
    assert (await client.delete("/texts/note", headers=headers)).status_code == 404


async def test_text_delete_requires_authentication(client: AsyncClient) -> None:
    assert (await client.delete("/texts/note")).status_code == 401


async def test_delete_user(client: AsyncClient) -> None:
    headers = await auth_headers(client)
    await client.put("/texts/note", json={"text": "bye"}, headers=headers)
    assert (await client.delete("/users/me", headers=headers)).json() == {"data": None}
    # The revoked token can neither read nor rewrite texts.
    assert (await client.get("/texts", headers=headers)).status_code == 401
    assert (await client.put("/texts/note", json={"text": "x"}, headers=headers)).status_code == 401
    # The name can be reused, and the new account does not inherit the old data.
    recreated = await auth_headers(client)
    assert (await client.get("/texts", headers=recreated)).json() == {"data": []}
    # The old token still does not work against the re-registered account.
    assert (await client.get("/texts", headers=headers)).status_code == 401


async def test_delete_user_requires_authentication(client: AsyncClient) -> None:
    assert (await client.delete("/users/me")).status_code == 401
    assert (
        await client.delete("/users/me", headers={"Authorization": "Bearer nope"})
    ).status_code == 401


async def test_delete_user_leaves_others_intact(client: AsyncClient) -> None:
    alice = await auth_headers(client, "alice")
    bob = await auth_headers(client, "bob")
    await client.put("/texts/note", json={"text": "alice-text"}, headers=alice)
    assert (await client.delete("/users/me", headers=alice)).status_code == 200
    assert (await client.get("/texts", headers=bob)).json() == {"data": []}
    await client.put("/texts/note", json={"text": "bob-text"}, headers=bob)
    assert (await client.get("/texts/note", headers=bob)).json() == {"data": "bob-text"}


@pytest.mark.parametrize(
    "path",
    ["/ping", "/users", "/sessions", "/sessions/current", "/texts", "/texts/note", "/users/me"],
)
async def test_wrong_method_precedes_authentication(client: AsyncClient, path: str) -> None:
    assert (await client.patch(path)).status_code == 405


class FakeClock:
    """Deterministic clock so expiry can be tested without sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def expiring_client() -> AsyncGenerator[tuple[AsyncClient, FakeClock]]:
    clock = FakeClock()
    app = create_app(token_ttl_seconds=300, clock=clock)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client, clock


async def test_session_expires_in_and_relogin(client: AsyncClient) -> None:
    await client.post("/users", json={"username": "alice", "password": "password1"})
    first = await client.post("/sessions", json={"username": "alice", "password": "password1"})
    assert first.json()["data"]["expires_in"] == 300
    second = await client.post("/sessions", json={"username": "alice", "password": "password1"})
    old = {"Authorization": f"Bearer {first.json()['data']['token']}"}
    new = {"Authorization": f"Bearer {second.json()['data']['token']}"}
    assert (await client.get("/texts", headers=old)).status_code == 401
    assert (await client.get("/texts", headers=new)).status_code == 200


async def test_token_expiry_http(expiring_client: tuple[AsyncClient, FakeClock]) -> None:
    client, clock = expiring_client
    headers = await auth_headers(client)
    assert (await client.get("/texts", headers=headers)).status_code == 200
    clock.now = 299.999
    assert (await client.get("/texts", headers=headers)).status_code == 200
    clock.now = 300.0
    assert (await client.get("/texts", headers=headers)).status_code == 401
    assert (await client.put("/texts/note", json={"text": "x"}, headers=headers)).status_code == 401
    assert (await client.delete("/users/me", headers=headers)).status_code == 401
    # Logging in again yields a usable token.
    fresh = await auth_headers(client)
    assert (await client.get("/texts", headers=fresh)).status_code == 200


async def test_failed_requests_do_not_break_later_requests(client: AsyncClient) -> None:
    # Each failing request type, then proof the connection still serves normally.
    assert (await client.post("/echo", json={"text": 42})).status_code == 400
    assert (await client.post("/echo", content=b"x" * 524289)).status_code == 413
    assert (await client.get("/no-such-route")).status_code == 404
    assert (await client.patch("/ping")).status_code == 405
    assert (await client.get("/texts")).status_code == 401
    assert (await client.get("/ping")).json() == {"data": "pong"}
    assert (await client.post("/echo", json={"text": "ok"})).json() == {"data": "ok"}


async def test_unexpected_handler_error_is_isolated(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Service.handle
    raised = False

    def flaky(
        self: Service, method: str, path: str, body: object, authorization: str
    ) -> tuple[int, dict[str, object]]:
        nonlocal raised
        if path == "/ping" and not raised:
            raised = True
            raise RuntimeError("boom")
        return original(self, method, path, body, authorization)

    monkeypatch.setattr(Service, "handle", flaky)
    try:
        await client.get("/ping")
    except RuntimeError:
        pass  # A real uvicorn server would answer 500 instead of re-raising.
    # The failure stays confined to that one request.
    assert (await client.get("/ping")).json() == {"data": "pong"}
    assert (await client.post("/echo", json={"text": "ok"})).json() == {"data": "ok"}


async def test_handler_runs_in_a_worker_thread(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    seen: list[int] = []
    original = Service.handle

    def recording(
        self: Service, method: str, path: str, body: object, authorization: str
    ) -> tuple[int, dict[str, object]]:
        seen.append(threading.get_ident())
        return original(self, method, path, body, authorization)

    monkeypatch.setattr(Service, "handle", recording)
    assert (await client.get("/ping")).status_code == 200
    # Blocking work must never run on the event loop thread.
    assert seen
    assert all(ident != loop_thread for ident in seen)


async def test_blocking_handler_does_not_stall_event_loop(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original = Service.handle

    def blocking(
        self: Service, method: str, path: str, body: object, authorization: str
    ) -> tuple[int, dict[str, object]]:
        if path == "/sessions":
            entered.set()
            release.wait(timeout=5)
        return original(self, method, path, body, authorization)

    monkeypatch.setattr(Service, "handle", blocking)
    await client.post("/users", json={"username": "alice", "password": "password1"})
    login = asyncio.create_task(
        client.post("/sessions", json={"username": "alice", "password": "password1"})
    )
    assert await asyncio.to_thread(entered.wait, 5)
    # While the login handler is blocked in a worker thread, the loop stays responsive.
    assert (await client.get("/ping")).status_code == 200
    assert not login.done()
    release.set()
    assert (await login).status_code == 200


async def test_concurrent_registration_over_http(client: AsyncClient) -> None:
    body = {"username": "alice", "password": "password1"}
    responses = await asyncio.gather(*(client.post("/users", json=body) for _ in range(8)))
    # Exactly one registration wins; the rest conflict, with no corrupted state.
    assert sorted(response.status_code for response in responses) == [201] + [409] * 7


async def test_concurrent_text_writes_over_http(client: AsyncClient) -> None:
    headers = await auth_headers(client)
    responses = await asyncio.gather(
        *(client.put(f"/texts/note{i}", json={"text": str(i)}, headers=headers) for i in range(20))
    )
    assert all(response.status_code == 200 for response in responses)
    listing = await client.get("/texts", headers=headers)
    # Names are ordered as strings, so "note10" sorts before "note2".
    assert listing.json() == {"data": sorted(f"note{i}" for i in range(20))}


async def test_concurrent_mixed_success_and_failure(client: AsyncClient) -> None:
    results = await asyncio.gather(
        client.get("/ping"),
        client.get("/missing"),
        client.patch("/ping"),
        client.post("/echo", json={"text": 1}),
        client.post("/echo", json={"text": "ok"}),
        client.get("/texts"),
    )
    # Failures in one request do not affect the outcome of the others.
    assert [result.status_code for result in results] == [200, 404, 405, 400, 200, 401]
