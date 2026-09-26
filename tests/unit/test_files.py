"""Tests for the registry and the HTTP route serving the files saved by ``download``."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from conftest import FakeCrawler
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import Message, Receive
from test_mcp_server import fake_factory, make_server

from crawl4tools.i18n import ENGLISH
from crawl4tools.server import ServerSettings, build_server, open_state
from crawl4tools.server.files import FILES_PATH, FileRegistry, file_url_base, files_route

# --- FileRegistry ---------------------------------------------------------------


def write(path: Path, data: bytes = b"data") -> Path:
    """Write *data* to *path*, creating its parent directories, and return *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_files_path() -> None:
    assert FILES_PATH == "/files/{token}"


def test_register_and_lookup(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "sub" / "page.md")
    token = registry.register(path)
    assert len(token) >= 32
    assert registry.lookup(token) == path.resolve()


def test_register_returns_a_new_token_each_time(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "page.md")
    first = registry.register(path)
    second = registry.register(path)
    assert first != second
    assert registry.lookup(first) == registry.lookup(second) == path.resolve()


def test_lookup_unknown_token(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    write(tmp_path / "page.md")
    assert registry.lookup("no-such-token") is None
    assert registry.lookup("") is None


def test_capacity_drops_the_oldest_token(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path, capacity=2)
    paths = [write(tmp_path / f"{i}.md") for i in range(3)]
    tokens = [registry.register(path) for path in paths]
    assert registry.lookup(tokens[0]) is None
    assert registry.lookup(tokens[1]) == paths[1].resolve()
    assert registry.lookup(tokens[2]) == paths[2].resolve()


@pytest.mark.parametrize("capacity", [0, -1], ids=["zero", "negative"])
def test_capacity_must_be_positive(tmp_path: Path, capacity: int) -> None:
    with pytest.raises(ValueError, match="capacity must be at least 1"):
        FileRegistry(tmp_path, capacity=capacity)


def test_lookup_of_a_deleted_file(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "page.md")
    token = registry.register(path)
    path.unlink()
    assert registry.lookup(token) is None


def test_lookup_of_a_directory(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "page.md")
    token = registry.register(path)
    path.unlink()
    path.mkdir()
    assert registry.lookup(token) is None


def test_lookup_of_a_symlink_out_of_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = write(tmp_path / "secret.txt")
    link = root / "link.txt"
    root.mkdir()
    link.symlink_to(outside)
    registry = FileRegistry(root)
    assert registry.lookup(registry.register(link)) is None


def test_lookup_of_a_file_replaced_by_a_symlink_out_of_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = write(tmp_path / "secret.txt")
    path = write(root / "page.md")
    registry = FileRegistry(root)
    token = registry.register(path)
    path.unlink()
    path.symlink_to(outside)
    assert registry.lookup(token) is None


def test_lookup_of_a_symlink_inside_the_root(tmp_path: Path) -> None:
    target = write(tmp_path / "data" / "page.md")
    link = tmp_path / "link.md"
    link.symlink_to(target)
    registry = FileRegistry(tmp_path)
    assert registry.lookup(registry.register(link)) == target.resolve()


def test_lookup_of_a_file_outside_the_root(tmp_path: Path) -> None:
    outside = write(tmp_path / "outside.md")
    registry = FileRegistry(tmp_path / "root")
    assert registry.lookup(registry.register(outside)) is None


def test_forget_removes_the_token(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "page.md")
    token = registry.register(path)
    other = registry.register(path)
    assert registry.forget(token) == path.resolve()
    assert registry.lookup(token) is None
    assert registry.forget(token) is None
    # Only the given token is forgotten, and the file itself is left alone.
    assert registry.lookup(other) == path.resolve()


def test_forget_an_unknown_token(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    assert registry.forget("no-such-token") is None


# --- file_url_base --------------------------------------------------------------


def http_request(headers: dict[str, str], *, scheme: str = "http") -> Request:
    """Return a Starlette request for ``/mcp`` with *headers*."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "server": ("127.0.0.1", 8765),
        "path": "/mcp",
        "query_string": b"",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
    }
    return Request(scope)


def test_file_url_base_without_request() -> None:
    assert file_url_base(None) is None


@pytest.mark.parametrize(
    "request_",
    [object(), SimpleNamespace(headers={"host": "a.example"}), SimpleNamespace(url=None)],
    ids=["plain-object", "no-url", "no-headers"],
)
def test_file_url_base_without_headers_or_url(request_: object) -> None:
    assert file_url_base(request_) is None


def test_file_url_base_without_host() -> None:
    assert file_url_base(http_request({})) is None


def test_file_url_base_from_the_host_header() -> None:
    assert file_url_base(http_request({"Host": "mcp.example:8765"})) == "http://mcp.example:8765"


def test_file_url_base_keeps_the_request_scheme() -> None:
    request = http_request({"host": "mcp.example"}, scheme="https")
    assert file_url_base(request) == "https://mcp.example"


def test_file_url_base_follows_the_forwarded_headers() -> None:
    request = http_request(
        {
            "Host": "127.0.0.1:8765",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "tools.example",
        }
    )
    assert file_url_base(request) == "https://tools.example"


def test_file_url_base_uses_the_first_forwarded_value() -> None:
    request = http_request(
        {
            "host": "127.0.0.1:8765",
            "x-forwarded-proto": "https, http",
            "x-forwarded-host": "tools.example, proxy.internal",
        }
    )
    assert file_url_base(request) == "https://tools.example"


def test_file_url_base_with_only_the_forwarded_host() -> None:
    request = http_request({"x-forwarded-host": "tools.example"})
    assert file_url_base(request) == "http://tools.example"


# --- GET /files/{token} ---------------------------------------------------------


def app_client(app: Any) -> httpx.AsyncClient:
    """Return an HTTP client talking to the ASGI *app* in process."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.parametrize(
    ("name", "content_type"),
    [("report.pdf", "application/pdf"), ("blob.crawl4tools-unknown", "application/octet-stream")],
    ids=["pdf", "unknown-extension"],
)
async def test_files_route_serves_a_registered_file(
    tmp_path: Path, name: str, content_type: str
) -> None:
    settings = ServerSettings(download_root=tmp_path)
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / name, b"%PDF-1.4 bytes")
    async with open_state(settings, fake_factory(FakeCrawler()), files=registry) as state:
        server = build_server(settings, state=state)
        token = registry.register(path)
        async with app_client(server.streamable_http_app()) as client:
            response = await client.get(f"/files/{token}")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 bytes"
    assert response.headers["content-type"] == content_type
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert name in disposition


async def test_files_route_unknown_token_is_404_json(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    async with app_client(server.streamable_http_app()) as client:
        response = await client.get("/files/no-such-token")
    assert response.status_code == 404
    assert response.json() == {"error": "not found"}


async def test_files_route_404_follows_the_language(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path, lang="ja")
    async with app_client(server.streamable_http_app()) as client:
        response = await client.get("/files/no-such-token")
    assert response.status_code == 404
    expected = ServerSettings(lang="ja").translator.gettext("not found")
    assert expected != "not found"
    assert response.json() == {"error": expected}


async def test_files_route_deleted_file_is_404(tmp_path: Path) -> None:
    settings = ServerSettings(download_root=tmp_path)
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "page.md")
    async with open_state(settings, fake_factory(FakeCrawler()), files=registry) as state:
        server = build_server(settings, state=state)
        token = registry.register(path)
        path.unlink()
        async with app_client(server.streamable_http_app()) as client:
            response = await client.get(f"/files/{token}")
    assert response.status_code == 404


# --- GET /files/{token}: deleting a file once it was delivered ---------------------
#
# These tests call the route at the ASGI level, so that they control what the
# client "says" through ``receive`` (staying connected, or disconnecting) and see
# every message the route sends.

#: More than three 64 KiB chunks, so that a delivery takes several sends.
BIG = bytes(range(256)) * 800

#: The logger of the route.
FILES_LOGGER = "crawl4tools.server.files"


@dataclass
class Exchange:
    """What the route sent for one request."""

    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    #: Whether the last body message (``more_body`` false) was sent.
    finished: bool = False


def connected_receive() -> Receive:
    """Return a receive of a client that sends its request and stays connected.

    The request comes first; after it, no message ever arrives (the call
    waits until it is cancelled), as with a client still reading the response.
    """
    requested = False

    async def receive() -> Message:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    return receive


def disconnecting_receive() -> Receive:
    """Return a receive of a client that sends its request, then goes away."""
    requested = False

    async def receive() -> Message:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    return receive


async def exchange(
    registry: FileRegistry,
    token: str,
    *,
    keep: bool = False,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    receive: Receive | None = None,
    spec_version: str = "2.4",
) -> Exchange:
    """Send one request for *token* to the route of *registry* and return its answer.

    *spec_version* is the ASGI HTTP spec version the server claims: below
    2.4, Starlette's streaming responses also listen for the disconnect
    themselves (uvicorn claims 2.3).
    """
    route = Route(FILES_PATH, files_route(registry, ENGLISH, keep=keep), methods=["GET"])
    app = Starlette(routes=[route])
    path = f"/files/{token}"
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            *((name.lower().encode(), value.encode()) for name, value in (headers or {}).items()),
        ],
    }
    result = Exchange()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = {
                name.decode("latin-1"): value.decode("latin-1")
                for name, value in message["headers"]
            }
        elif message["type"] == "http.response.body":
            result.body += message.get("body", b"")
            if not message.get("more_body", False):
                result.finished = True

    await app(scope, receive if receive is not None else connected_receive(), send)
    return result


def log_messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """Return the messages the route logged at *level*."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == FILES_LOGGER and record.levelno == level
    ]


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"], ids=["listening", "not-listening"])
async def test_a_delivered_file_is_deleted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, spec_version: str
) -> None:
    caplog.set_level(logging.INFO, logger=FILES_LOGGER)
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "sub" / "report.pdf", BIG)
    token = registry.register(path)
    resolved = path.resolve()

    result = await exchange(registry, token, spec_version=spec_version)

    assert result.status == 200
    assert result.finished
    assert result.body == BIG
    assert result.headers["content-type"] == "application/pdf"
    assert result.headers["content-length"] == str(len(BIG))
    assert result.headers["content-disposition"] == 'attachment; filename="report.pdf"'
    assert not path.exists()
    assert (tmp_path / "sub").is_dir(), "the emptied subdirectory is kept"
    assert registry.lookup(token) is None
    assert registry.forget(token) is None, "the token was forgotten"
    assert log_messages(caplog, logging.INFO) == [
        f"served: {resolved} ({len(BIG)} bytes)",
        f"deleted: {resolved}",
    ]
    again = await exchange(registry, token, spec_version=spec_version)
    assert again.status == 404


async def test_an_empty_file_is_delivered_and_deleted(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "empty.md", b"")
    token = registry.register(path)
    result = await exchange(registry, token)
    assert result.status == 200
    assert result.body == b""
    assert result.headers["content-length"] == "0"
    assert not path.exists()
    assert registry.lookup(token) is None


async def test_a_non_ascii_file_name_is_encoded(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "ページ.md")
    result = await exchange(registry, registry.register(path))
    assert result.status == 200
    assert result.headers["content-disposition"] == (
        "attachment; filename*=utf-8''%E3%83%9A%E3%83%BC%E3%82%B8.md"
    )


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"], ids=["listening", "not-listening"])
async def test_a_file_is_kept_when_the_client_disconnects(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, spec_version: str
) -> None:
    caplog.set_level(logging.INFO, logger=FILES_LOGGER)
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "report.pdf", BIG)
    token = registry.register(path)

    result = await exchange(
        registry, token, receive=disconnecting_receive(), spec_version=spec_version
    )

    assert len(result.body) < len(BIG), "the delivery stopped at the disconnect"
    assert BIG.startswith(result.body)
    assert path.read_bytes() == BIG
    assert registry.lookup(token) == path.resolve()
    assert log_messages(caplog, logging.INFO) == []
    # The client can fetch the file again, this time to the end.
    again = await exchange(registry, token, spec_version=spec_version)
    assert again.body == BIG
    assert not path.exists()


async def test_head_does_not_delete(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "report.pdf", BIG)
    token = registry.register(path)
    result = await exchange(registry, token, method="HEAD")
    assert result.status == 200
    assert result.body == b""
    assert result.headers["content-length"] == str(len(BIG))
    assert path.read_bytes() == BIG
    assert registry.lookup(token) == path.resolve()


async def test_a_range_request_does_not_delete(tmp_path: Path) -> None:
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "report.pdf", BIG)
    token = registry.register(path)
    result = await exchange(registry, token, headers={"Range": "bytes=0-3"})
    assert result.status == 206
    assert result.body == BIG[:4]
    assert path.read_bytes() == BIG
    assert registry.lookup(token) == path.resolve()


async def test_keep_leaves_a_delivered_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=FILES_LOGGER)
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "report.pdf", BIG)
    token = registry.register(path)
    for _ in range(2):
        result = await exchange(registry, token, keep=True)
        assert result.status == 200
        assert result.body == BIG
    assert path.read_bytes() == BIG
    assert registry.lookup(token) == path.resolve()
    resolved = path.resolve()
    assert log_messages(caplog, logging.INFO) == [
        f"served: {resolved} ({len(BIG)} bytes)",
        f"served: {resolved} ({len(BIG)} bytes)",
    ]


async def test_a_failed_deletion_keeps_the_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger=FILES_LOGGER)
    registry = FileRegistry(tmp_path)
    path = write(tmp_path / "report.pdf", BIG)
    token = registry.register(path)

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "unlink", refuse)
    result = await exchange(registry, token)

    assert result.body == BIG
    assert path.read_bytes() == BIG
    assert registry.lookup(token) == path.resolve()
    warnings = log_messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0].startswith(f"could not delete {path.resolve()}: ")
    assert "Permission denied" in warnings[0]
    assert not any(message.startswith("deleted:") for message in log_messages(caplog, logging.INFO))
