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
        return httpx.Response(200, json={"text": "Hello, world!"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "POST", "/echo", "example", {"text": "Hello, world!"}) == (
            200,
            {"text": "Hello, world!"},
        )
