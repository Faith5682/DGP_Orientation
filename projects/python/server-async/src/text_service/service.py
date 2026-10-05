"""In-memory baseline. Implement the task routes in handle()."""

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

ROUTES = (
    ("GET", "/ping"),
    ("POST", "/echo"),
    ("POST", "/users"),
    ("POST", "/sessions"),
    ("DELETE", "/sessions/current"),
    ("GET", "/texts"),
    ("PUT", "/texts/{name}"),
    ("GET", "/texts/{name}"),
    ("DELETE", "/texts/{name}"),
)

TEXT_PATH_RE = re.compile(r"^/texts/(?P<name>[^/]+)$")
TEXT_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
TEXT_PATH_METHODS = ("PUT", "GET", "DELETE")


def text_name(path: str) -> str | None:
    """Name segment for a /texts/{name} path, or None when the path does not match."""
    match = TEXT_PATH_RE.match(path)
    return match["name"] if match else None


def route_error(method: str, path: str) -> int | None:
    if text_name(path) is not None:
        return None if method in TEXT_PATH_METHODS else 405
    allowed = next((verb for verb, route in ROUTES if route == path), None)
    if allowed is None:
        return 404
    return None if method == allowed else 405


TEXT_MAX_BYTES = 65_536


def text_bytes(value: Any) -> bytes | None:
    """UTF-8 bytes for a valid text string, or None when the value is invalid."""
    if not isinstance(value, str):
        return None
    try:
        return value.encode("utf-8")
    except UnicodeError:  # unpaired surrogates are not valid Unicode
        return None


@dataclass
class User:
    salt: bytes
    digest: bytes
    token: str | None = None
    texts: dict[str, str] = field(default_factory=dict)


class Service:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.lock = threading.Lock()

    def handle(
        self, method: str, path: str, body: Any, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        if status := route_error(method, path):
            return status, {"message": "Not found" if status == 404 else "Method not allowed"}
        if method == "GET" and path == "/ping":
            return 200, {"data": "pong"}
        if method == "POST" and path == "/echo":
            if not isinstance(body, dict) or set(body) != {"text"}:
                return 400, {"message": "Expected text"}
            encoded = text_bytes(body["text"])
            if encoded is None:
                return 400, {"message": "text must be a string"}
            if len(encoded) > TEXT_MAX_BYTES:
                return 413, {"message": "Text too large"}
            return 200, {"data": body["text"]}
        if path in ("/users", "/sessions") and method == "POST":
            if not isinstance(body, dict) or set(body) != {"username", "password"}:
                return 400, {"message": "Expected username and password"}
            name, password = body["username"], body["password"]
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name)
                or not isinstance(password, str)
                or not 8 <= len(password) <= 128
            ):
                return 400, {"message": "Invalid username or password length"}
            try:
                password.encode("utf-8")
            except UnicodeError:
                return 400, {"message": "Password must be valid Unicode"}
            # Hashing is outside the state lock; commit/check against current state under lock.
            if path == "/users":
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
                with self.lock:
                    if name in self.users:
                        return 409, {"message": "Username exists"}
                    self.users[name] = User(salt, digest)
                return 201, {"data": {"username": name}}
            with self.lock:
                user = self.users.get(name)
                if user is None:
                    return 401, {"message": "Invalid username or password"}
                salt, expected = user.salt, user.digest
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
            with self.lock:
                if self.users.get(name) is not user or not hmac.compare_digest(digest, expected):
                    return 401, {"message": "Invalid username or password"}
                user.token = secrets.token_urlsafe(32)
                # Later server task: record a deadline and return expires_in.
                return 200, {"data": {"token": user.token}}
        name = text_name(path)
        text = ""
        if name is not None and method in ("PUT", "GET"):
            if not TEXT_NAME_RE.fullmatch(name):
                return 400, {"message": "Invalid text name"}
            if method == "PUT":
                if not isinstance(body, dict) or set(body) != {"text"}:
                    return 400, {"message": "Expected text"}
                encoded = text_bytes(body["text"])
                if encoded is None:
                    return 400, {"message": "text must be a string"}
                if len(encoded) > TEXT_MAX_BYTES:
                    return 413, {"message": "Text too large"}
                text = body["text"]
        protected = path in ("/texts", "/sessions/current") or (
            name is not None and method in ("PUT", "GET")
        )
        if protected:
            token = (
                authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            )
            with self.lock:
                user = next((u for u in self.users.values() if token and u.token == token), None)
                if user is None:
                    return 401, {"message": "Login required"}
                # Later server task: check token expiry here, before reading or modifying state.
                if name is not None and method in ("PUT", "GET"):
                    if method == "PUT":
                        user.texts[name] = text
                        return 200, {"data": None}
                    if name in user.texts:
                        return 200, {"data": user.texts[name]}
                    return 404, {"message": "Text not found"}
                if path == "/sessions/current" and method == "DELETE":
                    user.token = None
                    return 200, {"data": None}
                if path == "/texts" and method == "GET":
                    return 200, {"data": sorted(user.texts)}
        return 404, {"message": "Not found"}
