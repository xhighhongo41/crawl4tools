"""The Open WebUI external web loader app.

:func:`build_loader_app` builds a small Starlette app implementing Open
WebUI's external web loader contract: Open WebUI POSTs ``{"urls": [...]}``
to the configured URL (optionally with ``Authorization: Bearer <key>``)
and expects HTTP 200 with a JSON array of
``{"page_content": str, "metadata": dict}`` documents, where
``metadata["source"]`` is used for citations.

Any non-200 answer makes Open WebUI drop the whole batch, so per-URL
problems (invalid URLs, failed fetches, pages without text) never fail the
request: those URLs are logged and left out of the response. Only
malformed requests (400) and bad credentials (401) are refused.

The app has no lifespan of its own: it fetches through a
:class:`~crawl4tools.server.mcp_server.ServerState` owned by the host, so
it can share one fetcher and one semaphore with the MCP app.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from crawl4tools import __version__
from crawl4tools.engine.models import FetchOutcome, OutputFormat
from crawl4tools.engine.naming import dedupe_urls, validate_url
from crawl4tools.i18n import N_, LocalizedError, render_exception
from crawl4tools.server.mcp_server import ServerState, fetch_all
from crawl4tools.server.results import UrlsError

logger = logging.getLogger(__name__)

#: Default path of the crawl endpoint.
DEFAULT_LOADER_PATH = "/crawl"


@dataclass(frozen=True)
class LoaderSettings:
    """Settings of the web loader app.

    ``path`` is the path of the crawl endpoint, ``api_key`` (when set) is
    the bearer token every crawl request must present, and ``fit`` keeps
    only the main content of each page.
    """

    path: str = DEFAULT_LOADER_PATH
    api_key: str | None = None
    fit: bool = False


class CrawlRequest(BaseModel):
    """The body of a crawl request; unknown keys are ignored."""

    model_config = ConfigDict(strict=True)

    urls: list[str]


class RequestError(LocalizedError, ValueError):
    """The body of a crawl request is not a valid request.

    Also a ``ValueError``, so existing ``except ValueError`` clauses keep
    catching it; ``str()`` is the English message.
    """


def prepare_urls(urls: Sequence[str], max_urls: int) -> tuple[list[str], list[str]]:
    """Validate and deduplicate the URLs of one crawl request.

    Invalid URLs are not an error: they are returned in ``rejected`` (in
    input order, as given) and the caller simply skips them.

    Returns:
        ``(unique_valid, rejected)``: the stripped valid URLs, first-seen
        order and without duplicates, and the invalid URLs.

    Raises:
        UrlsError: (a ``ValueError``) if more than *max_urls* unique valid
            URLs remain.
    """
    valid: list[str] = []
    rejected: list[str] = []
    for url in urls:
        try:
            valid.append(validate_url(url))
        except ValueError:
            rejected.append(url)
    unique, _duplicates = dedupe_urls(valid)
    if len(unique) > max_urls:
        raise UrlsError(
            N_("too many URLs: {count} given, at most {limit} per request"),
            count=len(unique),
            limit=max_urls,
        )
    return unique, rejected


def loader_document(outcome: FetchOutcome, url: str) -> dict[str, Any] | None:
    """Return the loader document for *outcome* fetched from *url*.

    Returns:
        ``{"page_content": ..., "metadata": {...}}`` with ``source`` set to
        the requested *url*, or ``None`` if the fetch failed or produced no
        non-blank text (e.g. an image).
    """
    if not outcome.ok or outcome.text is None or outcome.text.strip() == "":
        return None
    return {
        "page_content": outcome.text,
        "metadata": {
            "source": url,
            "url": outcome.final_url or url,
            "title": outcome.title,
            "status_code": outcome.status_code,
            "content_type": outcome.content_type,
        },
    }


def _error(status_code: int, message: str, **extra: Any) -> JSONResponse:
    """Return a JSON error response ``{"error": message, **extra}``."""
    return JSONResponse({"error": message, **extra}, status_code=status_code)


def _authorized(request: Request, api_key: str) -> bool:
    """Return True if *request* carries ``Authorization: Bearer <api_key>``."""
    header = request.headers.get("authorization")
    if header is None:
        return False
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    # Headers are decoded as latin-1 by Starlette; re-encode to the raw bytes.
    return hmac.compare_digest(token.encode("latin-1"), api_key.encode("utf-8"))


async def _parse(request: Request) -> CrawlRequest:
    """Parse the body of a crawl request.

    Raises:
        RequestError: (a ``ValueError``) with a short reason if the body is
            not a valid request.
    """
    body = await request.body()
    try:
        data = json.loads(body)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise RequestError(N_("request body is not valid JSON")) from exc
    if not isinstance(data, dict):
        raise RequestError(N_('request body must be a JSON object like {{"urls": [...]}}'))
    if "urls" not in data:
        raise RequestError(N_('missing "urls"'))
    try:
        return CrawlRequest.model_validate(data)
    except ValidationError as exc:
        raise RequestError(N_('"urls" must be a list of strings')) from exc


def _log_skipped(outcome: FetchOutcome, url: str) -> None:
    """Log (always in English) why the fetch of *url* produced no document."""
    if outcome.ok:
        logger.warning("error: no text content: %s", url)
    elif outcome.error is not None:
        logger.warning("error: %s", outcome.error)
    else:
        logger.warning("error: fetch failed: %s", url)


def build_loader_app(state: ServerState, loader: LoaderSettings) -> Starlette:
    """Build the web loader app fetching through the shared *state*.

    Routes: ``POST loader.path`` crawls the posted URLs and ``GET /health``
    reports the version (never authenticated). Every other path answers
    404 JSON with a usage hint; the host owns the lifetime of *state*.
    """
    t = state.settings.translator
    hint = t.gettext('POST {path} with {{"urls": [...]}}').format(path=loader.path)

    async def crawl(request: Request) -> Response:
        if loader.api_key is not None and not _authorized(request, loader.api_key):
            return JSONResponse(
                {"error": t.gettext("unauthorized")},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            crawl_request = await _parse(request)
            urls, rejected = prepare_urls(crawl_request.urls, state.settings.max_urls)
        except ValueError as exc:
            return _error(400, render_exception(exc, t))
        for url in rejected:
            logger.warning("error: invalid URL: %s", url)
        if not urls:
            return JSONResponse([])

        options = state.settings.fetch_options(format=OutputFormat.MARKDOWN, fit=loader.fit)
        outcomes = await fetch_all(state, urls, options)
        documents: list[dict[str, Any]] = []
        for url, outcome in zip(urls, outcomes, strict=True):
            document = loader_document(outcome, url)
            if document is None:
                _log_skipped(outcome, url)
            else:
                documents.append(document)
        return JSONResponse(documents)

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    async def not_found(request: Request, exc: Exception) -> Response:
        return _error(404, t.gettext("not found"), hint=hint)

    async def method_not_allowed(request: Request, exc: Exception) -> Response:
        headers = exc.headers if isinstance(exc, HTTPException) else None
        return JSONResponse(
            {"error": t.gettext("method not allowed"), "hint": hint},
            status_code=405,
            headers=headers,
        )

    return Starlette(
        routes=[
            Route(loader.path, crawl, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
        ],
        exception_handlers={404: not_found, 405: method_not_allowed},
    )
