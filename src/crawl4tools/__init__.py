"""crawl4tools: web fetching tools built on crawl4ai."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("crawl4tools")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
