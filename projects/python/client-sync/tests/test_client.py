import time

import httpx
import pytest

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
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/echo"
        assert request.read().decode() == '{"text":"Hello, world!"}'
        return httpx.Response(200, json={"data": "Hello, world!"})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "POST", "/echo", "example", {"text": "Hello, world!"}) == (
            200,
            {"data": "Hello, world!"},
        )


def test_register() -> None:
    seen = {"count": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users"
        seen["count"] += 1
        if seen["count"] == 1:
            return httpx.Response(201, json={"data": {"username": "alice"}})
        return httpx.Response(409, json={"message": "Username exists"})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
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
    token = "tok"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            assert request.headers.get("Authorization") is None
            return httpx.Response(200, json={"data": {"token": token}})
        assert request.headers["Authorization"] == f"Bearer {token}"
        if request.method == "PUT" and request.url.path == "/texts/1":
            return httpx.Response(200, json={"data": None})
        assert request.method == "GET"
        assert request.url.path == "/texts/1"
        return httpx.Response(200, json={"data": "Hello, world!"})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(
            client, "POST", "/sessions", body={"username": "alice", "password": "password1"}
        )
        assert status == 200
        assert result["data"]["token"] == token
        assert exchange(client, "PUT", "/texts/1", token, {"text": "Hello, world!"}) == (
            200,
            {"data": None},
        )
        assert exchange(client, "GET", "/texts/1", token) == (
            200,
            {"data": "Hello, world!"},
        )


def test_delete() -> None:
    token = "tok"
    texts = {"1": "Hello, world!"}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"data": {"token": token}})
        assert request.headers["Authorization"] == f"Bearer {token}"
        if request.url.path == "/texts" and request.method == "GET":
            return httpx.Response(200, json={"data": sorted(texts)})
        assert request.url.path == "/texts/1"
        assert request.method == "DELETE"
        del texts["1"]
        return httpx.Response(200, json={"data": None})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(
            client, "POST", "/sessions", body={"username": "alice", "password": "password1"}
        )
        assert status == 200
        assert result["data"]["token"] == token
        assert "1" in exchange(client, "GET", "/texts", token)[1]["data"]
        assert exchange(client, "DELETE", "/texts/1", token) == (200, {"data": None})
        assert "1" not in exchange(client, "GET", "/texts", token)[1]["data"]


def test_delete_user() -> None:
    token = "tok"
    state = {"deleted": False}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"data": {"token": token}})
        assert request.url.path == "/users/me"
        assert request.method == "DELETE"
        if state["deleted"]:
            return httpx.Response(401, json={"message": "Please log in again"})
        state["deleted"] = True
        return httpx.Response(200, json={"data": None})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(
            client, "POST", "/sessions", body={"username": "alice", "password": "password1"}
        )
        assert status == 200
        assert result["data"]["token"] == token
        status, result = exchange(client, "DELETE", "/users/me", token)
        assert status == 200
        # main() 依据 401 清空 token 并提示重新登录
        assert exchange(client, "DELETE", "/users/me", token) == (
            401,
            {"message": "Please log in again"},
        )


def test_unauthenticated_list_returns_401() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(401, json={"message": "Login required"})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/texts")
        assert status == 401
        assert result == {"message": "Login required"}


def test_expired_token_returns_401() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer expired"
        return httpx.Response(401, json={"message": "Login required"})

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/texts", "expired")
        assert status == 401
        # main() 依据 401 打印 "Please log in again." 并清空 token
        assert result == {"message": "Login required"}


def test_error_body_missing_falls_back_to_text() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"")

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/texts")
        assert status == 404
        assert result == {"message": ""}


def test_error_body_not_json_falls_back_to_text() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"<html>Bad Gateway</html>")

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/ping")
        assert status == 502
        assert result == {"message": "<html>Bad Gateway</html>"}


def test_json_body_with_200_is_parsed() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='"plain string"')

    with httpx.Client(
        base_url="http://localhost:7878", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/ping")
        assert status == 200
        assert result == "plain string"


def test_connection_error_raises() -> None:
    with (
        httpx.Client(base_url="http://localhost:59999") as client,
        pytest.raises(httpx.ConnectError),
    ):
        exchange(client, "GET", "/ping")


def test_timeout_is_finite() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        time.sleep(0.05)
        return httpx.Response(200, json={"data": "pong"})

    timeout = httpx.Timeout(0.2)
    with httpx.Client(
        base_url="http://localhost:7878",
        timeout=timeout,
        transport=httpx.MockTransport(respond),
    ) as client:
        # 客户端使用有限超时
        assert client.timeout.connect == 0.2
        start = time.monotonic()
        assert exchange(client, "GET", "/ping") == (200, {"data": "pong"})
        assert time.monotonic() - start < 0.2


def test_timeout_applied_after_connect_failure() -> None:
    # 连接失败后（无服务器）客户端仍可继续，且有限超时使请求在有限时间内失败
    with httpx.Client(base_url="http://localhost:59999", timeout=httpx.Timeout(1)) as client:
        with pytest.raises(httpx.ConnectError):
            exchange(client, "GET", "/ping")
        start = time.monotonic()
        with pytest.raises(httpx.ConnectError):
            exchange(client, "GET", "/ping")
        assert time.monotonic() - start < 5
