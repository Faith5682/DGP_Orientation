from collections.abc import AsyncGenerator

import pytest
from httpx2 import ASGITransport, AsyncClient

from text_service.server import create_app

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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/users/me"),
        ("DELETE", "/texts/note"),
    ],
)
async def test_unimplemented_routes_are_absent(client: AsyncClient, method: str, path: str) -> None:
    assert (await client.request(method, path)).status_code == 404


@pytest.mark.parametrize(
    "path", ["/ping", "/users", "/sessions", "/sessions/current", "/texts", "/texts/note"]
)
async def test_wrong_method_precedes_authentication(client: AsyncClient, path: str) -> None:
    assert (await client.patch(path)).status_code == 405
