import argparse
import getpass
from typing import Any

import httpx


def exchange(
    client: httpx.Client, method: str, path: str, token: str = "", body: object = None
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, json=body, headers=headers)
    try:
        result = response.json()
    except ValueError:
        result = {"message": response.text}
    return response.status_code, result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7878")
    args = parser.parse_args()
    token = ""
    with httpx.Client(
        base_url=args.url, timeout=12, follow_redirects=False, trust_env=False
    ) as client:
        try:
            while True:
                command = input(
                    "ping / register / login / logout / list / echo / "
                    "delete-user / put / get / delete / q > "
                ).strip()
                body = None
                if command == "q":
                    break
                if command in ("register", "login"):
                    body = {
                        "username": input("username: "),
                        "password": getpass.getpass("password: "),
                    }
                    method, path = "POST", "/users" if command == "register" else "/sessions"
                elif command in ("ping", "logout", "list"):
                    method, path = {
                        "ping": ("GET", "/ping"),
                        "logout": ("DELETE", "/sessions/current"),
                        "list": ("GET", "/texts"),
                    }[command]
                elif command == "echo":
                    method, path = "POST", "/echo"
                    print(
                        "Text: enter . alone to finish; start a dot-leading line with an extra dot.\nNo implicit trailing newline; add a blank line to include one. . immediately means empty text."
                    )
                    lines = []
                    while True:
                        line = input("| ")
                        if line == ".":
                            break
                        if line.startswith(".."):
                            line = line[1:]
                        lines.append(line)
                    body = {"text": "\n".join(lines)}
                elif command in ("put", "get"):
                    name = input("Name: ")
                    print(
                        "Text: enter . alone to finish; start a dot-leading line with an extra dot.\nNo implicit trailing newline; add a blank line to include one. . immediately means empty text."
                    )
                    lines = []
                    if command == "put":
                        while True:
                            line = input("| ")
                            if line == ".":
                                break
                            if line.startswith(".."):
                                line = line[1:]
                            lines.append(line)
                    body = {"text": "\n".join(lines)} if command == "put" else None
                    method, path = {
                        "put": ("PUT", "/texts/" + name),
                        "get": ("GET", "/texts/" + name),
                    }[command]
                elif command == "delete":
                    name = input("Name: ")
                    method, path = "DELETE", "/texts/" + name
                elif command == "delete-user":
                    method, path = "DELETE", "/users/me"
                else:
                    print("Unknown command.")
                    continue
                try:
                    status, result = exchange(client, method, path, token, body)
                    print(status, result)
                    if command == "login" and status == 200:
                        token = result["data"]["token"]
                    if status == 401:
                        print("Please log in again.")
                    if status == 401 or (command == "logout" and status == 200):
                        token = ""
                    if command == "delete-user" and status == 200:
                        token = ""
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    print(f"Request failed: {exc}")
        except (EOFError, KeyboardInterrupt):
            print()
