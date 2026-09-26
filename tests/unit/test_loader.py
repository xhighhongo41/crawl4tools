from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from conftest import FakeCrawler, FakeHttp, make_result

from crawl4tools import __version__
from crawl4tools.engine.errors import HttpStatusError
from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.engine.models import FetchOptions, FetchOutcome
from crawl4tools.server import LoaderSettings, ServerSettings, build_loader_app, open_state
from crawl4tools.server.loader import loader_document, prepare_urls

URL = "https://example.com/page"
URL2 = "https://example.com/other"
URL3 = "https://example.com/third"
KEY = "s3cret-loader-key"

# --- helpers ------------------------------------------------------------------


@asynccontextmanager
async def loader_client(
    *,
    crawler: FakeCrawler | None = None,
    loader: LoaderSettings | None = None,
    **settings: Any,
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeCrawler]]:
    """Yield an HTTP client for a loader app backed by fake crawler/HTTP doubles."""
    fake = crawler if crawler is not None else FakeCrawler()
    fake_http = FakeHttp()

    def factory(options: FetchOptions) -> Fetcher:
        return Fetcher(options, crawler_factory=fake.factory, http_client_factory=fake_http)

    async with open_state(ServerSettings(**settings), factory) as state:
        app = build_loader_app(state, loader if loader is not None else LoaderSettings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, fake


async def crawl(client: httpx.AsyncClient, urls: Any, **kwargs: Any) -> httpx.Response:
    """POST ``{"urls": urls}`` to the default loader path."""
    return await client.post("/crawl", json={"urls": urls}, **kwargs)


def outcome(**fields: Any) -> FetchOutcome:
    """Return a FetchOutcome for :data:`URL` with *fields* overridden."""
    values: dict[str, Any] = {"url": URL, "ok": True}
    values.update(fields)
    return FetchOutcome(**values)


# --- prepare_urls -----------------------------------------------------------------


def test_prepare_urls_valid_and_deduped() -> None:
    assert prepare_urls([URL, f" {URL2} ", URL], 5) == ([URL, URL2], [])


def test_prepare_urls_rejects_invalid_in_input_order() -> None:
    unique, rejected = prepare_urls(["not a url", URL, "ftp://x", ""], 5)
    assert unique == [URL]
    assert rejected == ["not a url", "ftp://x", ""]


def test_prepare_urls_too_many() -> None:
    with pytest.raises(ValueError, match="too many URLs: 3 given, at most 2 per request"):
        prepare_urls([URL, URL2, URL3], 2)


def test_prepare_urls_counts_unique_only() -> None:
    assert prepare_urls([URL, URL2, URL, URL2, "bad"], 2) == ([URL, URL2], ["bad"])


def test_prepare_urls_empty() -> None:
    assert prepare_urls([], 1) == ([], [])


# --- loader_document --------------------------------------------------------------


def test_loader_document_ok_with_text() -> None:
    doc = loader_document(
        outcome(
            final_url=URL2,
            text="# Hi",
            title="T",
            status_code=200,
            content_type="text/html",
        ),
        URL,
    )
    assert doc == {
        "page_content": "# Hi",
        "metadata": {
            "source": URL,
            "url": URL2,
            "title": "T",
            "status_code": 200,
            "content_type": "text/html",
        },
    }


def test_loader_document_url_falls_back_to_requested() -> None:
    doc = loader_document(outcome(text="x"), URL)
    assert doc is not None
    assert doc["metadata"]["url"] == URL
    assert doc["metadata"]["title"] is None


def test_loader_document_none_text() -> None:
    assert loader_document(outcome(text=None, data=b"\x89PNG"), URL) is None


def test_loader_document_whitespace_text() -> None:
    assert loader_document(outcome(text=" \n\t "), URL) is None


def test_loader_document_failed() -> None:
    failed = outcome(ok=False, text="ignored", error=HttpStatusError(URL, 404))
    assert loader_document(failed, URL) is None


# --- crawl endpoint: success ------------------------------------------------------


async def test_crawl_two_urls_in_input_order() -> None:
    async with loader_client() as (client, _):
        response = await crawl(client, [URL2, URL])
    assert response.status_code == 200
    docs = response.json()
    assert [doc["metadata"]["source"] for doc in docs] == [URL2, URL]
    for doc, url in zip(docs, [URL2, URL], strict=True):
        assert doc["page_content"] == "# Hello"
        assert doc["metadata"] == {
            "source": url,
            "url": url,
            "title": "Example Domain",
            "status_code": 200,
            "content_type": "text/html",
        }


async def test_crawl_logs_a_fetched_line_per_document(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="crawl4tools.server.loader")
    async with loader_client() as (client, _):
        response = await crawl(client, [URL])
    assert response.status_code == 200
    assert f"fetched: {URL} (HTTP 200, html, 7 chars)" in caplog.text


async def test_crawl_duplicate_url_returned_once() -> None:
    async with loader_client() as (client, fake):
        response = await crawl(client, [URL, URL])
    assert response.status_code == 200
    assert [doc["metadata"]["source"] for doc in response.json()] == [URL]
    assert [url for url, _ in fake.calls] == [URL]


async def test_crawl_invalid_urls_omitted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="crawl4tools.server.loader")
    async with loader_client() as (client, fake):
        response = await crawl(client, ["ftp://x", URL, "not a url"])
    assert response.status_code == 200
    assert [doc["metadata"]["source"] for doc in response.json()] == [URL]
    assert [url for url, _ in fake.calls] == [URL]
    assert "error: invalid URL: ftp://x" in caplog.text
    assert "error: invalid URL: not a url" in caplog.text


async def test_crawl_failed_url_omitted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="crawl4tools.server.loader")
    crawler = FakeCrawler({URL: make_result(status_code=404), URL2: make_result()})
    async with loader_client(crawler=crawler) as (client, _):
        response = await crawl(client, [URL, URL2])
    assert response.status_code == 200
    assert [doc["metadata"]["source"] for doc in response.json()] == [URL2]
    assert "error: HTTP 404" in caplog.text


async def test_crawl_all_failed_returns_empty_list() -> None:
    crawler = FakeCrawler(make_result(status_code=500))
    async with loader_client(crawler=crawler) as (client, _):
        response = await crawl(client, [URL, URL2])
    assert response.status_code == 200
    assert response.json() == []


async def test_crawl_ok_without_text_omitted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="crawl4tools.server.loader")
    crawler = FakeCrawler(make_result(raw_markdown="   "))
    async with loader_client(crawler=crawler) as (client, _):
        response = await crawl(client, [URL])
    assert response.status_code == 200
    assert response.json() == []
    assert f"error: no text content: {URL}" in caplog.text


async def test_crawl_empty_urls_fetches_nothing() -> None:
    async with loader_client() as (client, fake):
        response = await crawl(client, [])
    assert response.status_code == 200
    assert response.json() == []
    assert fake.calls == []


async def test_crawl_extra_keys_ignored() -> None:
    async with loader_client() as (client, _):
        response = await client.post("/crawl", json={"urls": [URL], "extra": 1})
    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_crawl_fit_reaches_fetch_options() -> None:
    async with loader_client(loader=LoaderSettings(fit=True)) as (client, _):
        response = await crawl(client, [URL])
    assert response.status_code == 200
    assert response.json()[0]["page_content"] == "# Hello (fit)"


async def test_crawl_custom_path() -> None:
    async with loader_client(loader=LoaderSettings(path="/load")) as (client, _):
        loaded = await client.post("/load", json={"urls": [URL]})
        default = await client.post("/crawl", json={"urls": [URL]})
    assert loaded.status_code == 200
    assert len(loaded.json()) == 1
    assert default.status_code == 404
    assert "/load" in default.json()["hint"]


# --- crawl endpoint: client errors ------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"",
        b"[1, 2]",
        b'["https://example.com/"]',
        b"{}",
        b'{"url": ["https://example.com/"]}',
        b'{"urls": [1]}',
        b'{"urls": ["https://example.com/", null]}',
        b'{"urls": "x"}',
        b'{"urls": null}',
        b"\xff\xfe",
    ],
)
async def test_crawl_bad_body_is_400(body: bytes) -> None:
    async with loader_client() as (client, fake):
        response = await client.post(
            "/crawl", content=body, headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    assert isinstance(response.json()["error"], str)
    assert response.json()["error"]
    assert fake.calls == []


async def test_crawl_too_many_urls_is_400() -> None:
    async with loader_client(max_urls=2) as (client, fake):
        response = await crawl(client, [URL, URL2, URL3])
    assert response.status_code == 400
    assert response.json() == {"error": "too many URLs: 3 given, at most 2 per request"}
    assert fake.calls == []


async def test_crawl_duplicates_do_not_count_toward_limit() -> None:
    async with loader_client(max_urls=2) as (client, _):
        response = await crawl(client, [URL, URL2, URL, URL2])
    assert response.status_code == 200
    assert len(response.json()) == 2


# --- auth -------------------------------------------------------------------------


def assert_unauthorized(response: httpx.Response) -> None:
    """Assert a 401 answer that never leaks the API key."""
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert KEY not in response.text
    for name, value in response.headers.items():
        assert KEY not in name
        assert KEY not in value


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Bearer {KEY}x"},
        {"Authorization": f"Basic {KEY}"},
        {"Authorization": KEY},
        {"Authorization": f"Bearer{KEY}"},
    ],
)
async def test_crawl_auth_rejected(
    headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with loader_client(loader=LoaderSettings(api_key=KEY)) as (client, fake):
        response = await crawl(client, [URL], headers=headers)
    assert_unauthorized(response)
    assert fake.calls == []
    assert KEY not in caplog.text


async def test_crawl_auth_rejected_before_body_parsing() -> None:
    async with loader_client(loader=LoaderSettings(api_key=KEY)) as (client, _):
        response = await client.post("/crawl", content=b"not json")
    assert_unauthorized(response)


async def test_crawl_auth_non_ascii_header_rejected() -> None:
    async with loader_client(loader=LoaderSettings(api_key=KEY)) as (client, _):
        response = await crawl(
            client, [URL], headers={"Authorization": "Bearer é".encode("latin-1")}
        )
    assert_unauthorized(response)


async def test_crawl_auth_accepted() -> None:
    async with loader_client(loader=LoaderSettings(api_key=KEY)) as (client, _):
        response = await crawl(client, [URL], headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_crawl_no_key_accepts_any_header() -> None:
    async with loader_client() as (client, _):
        bare = await crawl(client, [URL])
        empty = await crawl(client, [URL], headers={"Authorization": "Bearer "})
        other = await crawl(client, [URL], headers={"Authorization": "Bearer whatever"})
    assert [r.status_code for r in (bare, empty, other)] == [200, 200, 200]


# --- health and unknown paths -----------------------------------------------------


async def test_health() -> None:
    async with loader_client() as (client, _):
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_health_needs_no_auth() -> None:
    async with loader_client(loader=LoaderSettings(api_key=KEY)) as (client, _):
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_unknown_path_is_404_json() -> None:
    async with loader_client() as (client, _):
        response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {
        "error": "not found",
        "hint": 'POST /crawl with {"urls": [...]}',
    }


async def test_wrong_method_is_405_json() -> None:
    async with loader_client() as (client, _):
        response = await client.get("/crawl")
    assert response.status_code == 405
    assert "error" in response.json()


async def test_english_hint_uses_single_braces() -> None:
    """Guard against the doubled braces in the hint template leaking through."""
    async with loader_client() as (client, _):
        response = await client.get("/nope")
    assert response.json()["hint"] == 'POST /crawl with {"urls": [...]}'


# --- message language ---------------------------------------------------------

_REQUEST_NOT_JSON_JA = "リクエストボディが正しい JSON ではありません"
_REQUEST_NOT_OBJECT_JA = (
    'リクエストボディは {"urls": [...]} のような JSON オブジェクトにしてください'
)
_MISSING_URLS_JA = '"urls" がありません'
_URLS_TYPE_JA = '"urls" は文字列のリストにしてください'
_TOO_MANY_JA = "URL が多すぎます: 3 件(1 リクエストあたり最大 2 件)"
_UNAUTHORIZED_JA = "認証されていません"
_NOT_FOUND_JA = "見つかりません"
_METHOD_NOT_ALLOWED_JA = "許可されていないメソッドです"
_HINT_JA = '/crawl に {"urls": [...]} を POST してください'


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"not json", _REQUEST_NOT_JSON_JA),
        (b"[1, 2]", _REQUEST_NOT_OBJECT_JA),
        (b"{}", _MISSING_URLS_JA),
        (b'{"urls": "x"}', _URLS_TYPE_JA),
    ],
    ids=["not-json", "not-object", "missing-urls", "wrong-type"],
)
async def test_crawl_bad_body_error_is_japanese(body: bytes, expected: str) -> None:
    async with loader_client(lang="ja") as (client, _):
        response = await client.post(
            "/crawl", content=body, headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    assert response.json() == {"error": expected}


async def test_crawl_too_many_urls_error_is_japanese() -> None:
    async with loader_client(lang="ja", max_urls=2) as (client, _):
        response = await crawl(client, [URL, URL2, URL3])
    assert response.status_code == 400
    assert response.json() == {"error": _TOO_MANY_JA}


async def test_crawl_unauthorized_is_japanese() -> None:
    async with loader_client(lang="ja", loader=LoaderSettings(api_key=KEY)) as (client, _):
        response = await crawl(client, [URL])
    assert response.status_code == 401
    assert response.json() == {"error": _UNAUTHORIZED_JA}
    assert response.headers["www-authenticate"] == "Bearer"


async def test_unknown_path_is_japanese() -> None:
    async with loader_client(lang="ja") as (client, _):
        response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"error": _NOT_FOUND_JA, "hint": _HINT_JA}


async def test_wrong_method_is_japanese() -> None:
    async with loader_client(lang="ja") as (client, _):
        response = await client.get("/crawl")
    assert response.status_code == 405
    assert response.json() == {"error": _METHOD_NOT_ALLOWED_JA, "hint": _HINT_JA}


async def test_health_identical_regardless_of_language() -> None:
    async with loader_client(lang="ja") as (client, _):
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_japanese_lang_keeps_english_logs_for_failed_and_invalid_urls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="crawl4tools.server.loader")
    crawler = FakeCrawler({URL: make_result(status_code=404), URL2: make_result()})
    async with loader_client(lang="ja", crawler=crawler) as (client, _):
        response = await crawl(client, [URL, URL2, "not a url"])
    assert response.status_code == 200
    assert "error: HTTP 404" in caplog.text
    assert "error: invalid URL: not a url" in caplog.text


async def test_japanese_lang_keeps_the_fetched_log_line_in_english(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="crawl4tools.server.loader")
    async with loader_client(lang="ja") as (client, _):
        response = await crawl(client, [URL])
    assert response.status_code == 200
    assert f"fetched: {URL} (HTTP 200, html, 7 chars)" in caplog.text
