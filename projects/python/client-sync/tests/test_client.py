import json

import httpx

from text_service.client import exchange


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})


def test_echo() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/echo"
        assert request.headers["Authorization"] == "Bearer example"
        assert json.loads(request.content) == {"text": "Hello, world!"}
        return httpx.Response(200, json={"data": "Hello, world!"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "POST", "/echo", "example", {"text": "Hello, world!"}) == (
            200,
            {"data": "Hello, world!"},
        )


def test_register() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users"
        assert json.loads(request.content) == {"username": "alice", "password": "secret"}
        return httpx.Response(201, json={"data": {"username": "alice"}})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(
            client, "POST", "/users", body={"username": "alice", "password": "secret"}
        ) == (
            201,
            {"data": {"username": "alice"}},
        )


def test_put_and_get() -> None:
    def respond_login(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions"
        assert json.loads(request.content) == {"username": "alice", "password": "secret"}
        return httpx.Response(200, json={"data": {"token": "example", "expires": 300}})

    def respond_put(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts/1"
        assert request.headers["Authorization"] == "Bearer example"
        assert json.loads(request.content) == {"text": "Hello, world!"}
        return httpx.Response(201, json={"data": None})

    def respond_get(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts/1"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": {"text": "Hello, world!"}})

    with httpx.Client(
        base_url="http://localhost",
        transport=httpx.MockTransport(
            lambda request: (
                respond_login(request)
                if request.url.path == "/sessions"
                else respond_put(request)
                if request.url.path == "/texts/1" and request.method == "PUT"
                else respond_get(request)
            )
        ),
    ) as client:
        status, result = exchange(
            client, "POST", "/sessions", body={"username": "alice", "password": "secret"}
        )
        assert status == 200
        token = result["data"]["token"]
        assert exchange(client, "PUT", "/texts/1", token, {"text": "Hello, world!"}) == (
            201,
            {"data": None},
        )
        assert exchange(client, "GET", "/texts/1", token) == (
            200,
            {"data": {"text": "Hello, world!"}},
        )

def test_delete() -> None:
    def respond_delete(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts/1"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": None})
    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond_delete)
    ) as client:
        assert exchange(client, "DELETE", "/texts/1", "example") == (200, {"data": None})
