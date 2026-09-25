"""Serve the files saved by the ``download`` tool over HTTP.

The ``download`` tool saves files on the server's machine, out of reach
of a remote MCP client. :class:`FileRegistry` hands out an unguessable
token for each saved file, and the ``GET /files/{token}`` route built by
:func:`files_route` serves the file for it, so that an agent can fetch
the file to its own machine (e.g. with ``curl -o``) without passing its
content through the conversation. :func:`file_url_base` builds the public
base of those URLs from the MCP request.
"""

from __future__ import annotations

import mimetypes
import secrets
from collections import OrderedDict
from typing import TYPE_CHECKING

from starlette.responses import FileResponse, JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from starlette.requests import Request
    from starlette.responses import Response

    from crawl4tools.i18n import Translator

#: Path of the route serving a registered file.
FILES_PATH = "/files/{token}"

#: Bytes of randomness in each token (43 URL-safe characters).
_TOKEN_BYTES = 32


class FileRegistry:
    """Map unguessable tokens to the files saved under a download root.

    The newest *capacity* tokens are kept; registering one more forgets
    the oldest. Tokens do not expire otherwise. A token only resolves to
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


def files_route(registry: FileRegistry, t: Translator) -> Callable[[Request], Awaitable[Response]]:
    """Return the endpoint of :data:`FILES_PATH` serving the files of *registry*.

    A known token answers the file as an attachment named after it; any
    other token answers 404 JSON whose ``error`` value is translated by *t*.
    """

    async def serve_file(request: Request) -> Response:
        path = registry.lookup(request.path_params["token"])
        if path is None:
            return JSONResponse({"error": t.gettext("not found")}, status_code=404)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name)

    return serve_file
