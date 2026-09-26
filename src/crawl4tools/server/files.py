"""Serve the files saved by the ``download`` tool over HTTP.

The ``download`` tool saves files on the server's machine, out of reach
of a remote MCP client. :class:`FileRegistry` hands out an unguessable
token for each saved file, and the ``GET /files/{token}`` route built by
:func:`files_route` serves the file for it, so that an agent can fetch
the file to its own machine (e.g. with ``curl -o``) without passing its
content through the conversation. :func:`file_url_base` builds the public
base of those URLs from the MCP request.

A file sent whole to a client is deleted from the server, unless the route
is told to keep the files. Logs are in English.
"""

from __future__ import annotations

import logging
import mimetypes
import secrets
from collections import OrderedDict
from typing import TYPE_CHECKING
from urllib.parse import quote

from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from starlette.requests import Request
    from starlette.responses import Response

    from crawl4tools.i18n import Translator

logger = logging.getLogger(__name__)

#: Path of the route serving a registered file.
FILES_PATH = "/files/{token}"

#: Bytes of randomness in each token (43 URL-safe characters).
_TOKEN_BYTES = 32

#: Bytes read and sent at a time when a file is delivered whole.
_CHUNK_SIZE = 64 * 1024


class FileRegistry:
    """Map unguessable tokens to the files saved under a download root.

    The newest *capacity* tokens are kept; registering one more forgets
    the oldest. Tokens do not expire otherwise, but can be forgotten with
    :meth:`forget`. A token only resolves to
    a file that still exists and still lies inside *root*.
    """

    def __init__(self, root: Path, *, capacity: int = 10_000) -> None:
        """Keep the tokens of files under *root*, at most *capacity* of them.

        Raises:
            ValueError: if *capacity* is less than 1.
        """
        if capacity < 1:
            raise ValueError(f"capacity must be at least 1, got {capacity}")
        self.root = root
        self.capacity = capacity
        self._paths: OrderedDict[str, Path] = OrderedDict()

    def register(self, path: Path) -> str:
        """Return a new token for *path*; the same path gets a new token on each call."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._paths[token] = path.resolve()
        while len(self._paths) > self.capacity:
            self._paths.popitem(last=False)
        return token

    def forget(self, token: str) -> Path | None:
        """Forget *token* and return the path it was registered for, or None if unknown.

        The file itself is left alone, as are the other tokens of the same file.
        """
        return self._paths.pop(token, None)

    def lookup(self, token: str) -> Path | None:
        """Return the file of *token*, or None.

        None is returned when *token* is unknown (or forgotten), or when
        its file is no longer a regular file inside the root, e.g. it was
        deleted or replaced by a symlink leading out of the root.
        """
        path = self._paths.get(token)
        if path is None:
            return None
        try:
            # Resolve again: the file may have been replaced since it was registered.
            resolved = path.resolve()
            root = self.root.resolve()
            inside = resolved == root or resolved.is_relative_to(root)
            if not inside or not resolved.is_file():
                return None
        except (OSError, RuntimeError):
            # OSError: e.g. no permission to inspect the path. RuntimeError: a
            # symlink loop (Python < 3.13).
            return None
        return resolved


def _first_value(value: object) -> str | None:
    """Return the first comma-separated item of a header *value*, or None if empty."""
    if not isinstance(value, str):
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def file_url_base(request: object | None) -> str | None:
    """Return ``"{scheme}://{host}"`` of the MCP HTTP *request*, or None.

    *request* is the Starlette request of an MCP call over HTTP (None over
    stdio). The scheme is ``X-Forwarded-Proto`` if present, else the
    request's own; the host is ``X-Forwarded-Host`` if present, else the
    ``Host`` header. With several comma-separated values (a chain of
    proxies), the first one is used. None is returned when *request* is
    None, has no ``headers`` or ``url``, or no host can be found.
    """
    if request is None:
        return None
    # Read the attributes defensively, as the MCP SDK does for request headers:
    # the transport decides what object, if any, is attached to the call.
    get_header = getattr(getattr(request, "headers", None), "get", None)
    url = getattr(request, "url", None)
    if not callable(get_header) or url is None:
        return None
    host = _first_value(get_header("x-forwarded-host")) or _first_value(get_header("host"))
    if host is None:
        return None
    scheme = _first_value(get_header("x-forwarded-proto")) or _first_value(
        getattr(url, "scheme", None)
    )
    if scheme is None:
        return None
    return f"{scheme}://{host}"


def _content_disposition(name: str) -> str:
    """Return the ``Content-Disposition`` of an attachment named *name*.

    The value is the one Starlette's ``FileResponse`` writes: a plain
    ``filename`` when *name* needs no quoting, else an RFC 5987
    ``filename*`` in UTF-8.
    """
    quoted = quote(name)
    if quoted != name:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{name}"'


class _Delivery:
    """The whole delivery of one registered file, deleted once it is complete.

    :meth:`body` streams the file and records whether it was sent to the
    end without the client disconnecting; :meth:`finish`, run once the
    response is over, then deletes the file and forgets its token (unless
    *keep*).
    """

    def __init__(
        self,
        request: Request,
        registry: FileRegistry,
        token: str,
        path: Path,
        size: int,
        *,
        keep: bool,
    ) -> None:
        """Prepare to send the *size* bytes of *path*, registered as *token*."""
        self.request = request
        self.registry = registry
        self.token = token
        self.path = path
        self.size = size
        self.keep = keep
        #: Whether every byte was sent without seeing the client disconnect.
        self.complete = False

    async def body(self) -> AsyncIterator[bytes]:
        """Yield the file in chunks, stopping early if the client disconnects.

        At most :attr:`size` bytes (the announced ``Content-Length``) are
        sent; a file that shrank meanwhile ends the body early too. Either
        way the delivery is then not complete.
        """
        sent = 0
        with self.path.open("rb") as file:
            while sent < self.size:
                chunk = await run_in_threadpool(file.read, min(_CHUNK_SIZE, self.size - sent))
                if not chunk:
                    return
                # The server may drop what is sent to a client that has gone
                # (uvicorn does), so a body sent to the end would not prove the
                # client received it: look for the disconnect before each chunk.
                if await self.request.is_disconnected():
                    return
                yield chunk
                sent += len(chunk)
        self.complete = True

    async def finish(self) -> None:
        """Log a complete delivery and delete the file and its token, unless kept.

        Does nothing if the delivery was not complete. If the file cannot
        be deleted, a warning is logged and the token is kept.
        """
        if not self.complete:
            return
        logger.info("served: %s (%d bytes)", self.path, self.size)
        if self.keep:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not delete %s: %s", self.path, exc)
            return
        self.registry.forget(self.token)
        logger.info("deleted: %s", self.path)


def files_route(
    registry: FileRegistry, t: Translator, *, keep: bool = False
) -> Callable[[Request], Awaitable[Response]]:
    """Return the endpoint of :data:`FILES_PATH` serving the files of *registry*.

    A known token answers the file as an attachment named after it; any
    other token answers 404 JSON whose ``error`` value is translated by *t*.

    Once a ``GET`` without a ``Range`` header has sent the whole file
    without seeing the client disconnect, the file is deleted and its
    token forgotten, so the URL answers 404 from then on; with *keep*,
    both stay. A ``HEAD`` or a ``Range`` request never deletes anything.
    """

    async def serve_file(request: Request) -> Response:
        token = request.path_params["token"]
        path = registry.lookup(token)
        if path is None:
            return JSONResponse({"error": t.gettext("not found")}, status_code=404)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if request.method == "HEAD" or "range" in request.headers:
            return FileResponse(path, media_type=media_type, filename=path.name)
        try:
            size = path.stat().st_size
        except OSError:
            # The file went away since the lookup.
            return JSONResponse({"error": t.gettext("not found")}, status_code=404)
        delivery = _Delivery(request, registry, token, path, size, keep=keep)
        headers = {
            "content-disposition": _content_disposition(path.name),
            "content-length": str(size),
        }
        return StreamingResponse(
            delivery.body(),
            headers=headers,
            media_type=media_type,
            background=BackgroundTask(delivery.finish),
        )

    return serve_file
