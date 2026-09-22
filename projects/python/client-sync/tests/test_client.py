import httpx

from text_service.client import exchange


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})


def test_echo() -> None:
    with httpx.Client(base_url="http://localhost:7878") as client:
        assert exchange(client, "POST", "/echo", "example", {"text": "Hello, world!"}) == (
            200,
            {"data": "Hello, world!"},
        )


def test_register() -> None:
    with httpx.Client(base_url="http://localhost:7878") as client:
        assert exchange(
            client, "POST", "/users", body={"username": "alice", "password": "password1"}
        ) == (
            201,
            {"data": {"username": "alice"}},
        )
        assert exchange(
            client, "POST", "/users", body={"username": "alice", "password": "password1"}
        ) == (
            409,
            {"message": "Username exists"},
        )


def test_put_and_get() -> None:
    with httpx.Client(base_url="http://localhost:7878") as client:
        status, result = exchange(
            client, "POST", "/sessions", body={"username": "alice", "password": "password1"}
        )
        assert status == 200
        token = result["data"]["token"]
        assert exchange(client, "PUT", "/texts/1", token, {"text": "Hello, world!"}) == (
            200,
            {"data": None},
        )
        assert exchange(client, "GET", "/texts/1", token) == (
            200,
            {"data": "Hello, world!"},
        )


def test_delete() -> None:
    with httpx.Client(base_url="http://localhost:7878") as client:
        status, result = exchange(
            client, "POST", "/sessions", body={"username": "alice", "password": "password1"}
        )
        assert status == 200
        token = result["data"]["token"]
        assert exchange(client, "DELETE", "/texts/1", token) == (200, {"data": None})
