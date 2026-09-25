"""Tests for the registry and the HTTP route serving the files saved by ``download``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from conftest import FakeCrawler
from starlette.requests import Request
from test_mcp_server import fake_factory, make_server

from crawl4tools.server import ServerSettings, build_server, open_state
from crawl4tools.server.files import FILES_PATH, FileRegistry, file_url_base

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
