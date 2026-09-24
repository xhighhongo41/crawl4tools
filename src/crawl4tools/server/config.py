"""Loading of the crawl4mcp YAML/JSON config file.

Since YAML is a strict superset of JSON, a single :func:`yaml.safe_load`
call handles both formats. Values are returned untouched; converting them
to the types the ``crawl4mcp`` click command expects happens later, so
this module has no knowledge of click.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

#: The click option names accepted in a config file, i.e. every top-level
#: key that :mod:`crawl4tools.server.mcp_main` will eventually recognize.
CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "transport",
        "host",
        "port",
        "path",
        "proxy",
        "fallback",
        "timeout",
        "concurrency",
        "max_urls",
        "download_dir",
        "verbose",
    }
)


class ConfigError(ValueError):
    """Raised when a crawl4mcp config file cannot be read or is invalid."""


def load_config(path: Path) -> dict[str, Any]:
    """Load and validate the crawl4mcp config file at *path*.

    Accepts both YAML and JSON (JSON is valid YAML). An empty file yields
    an empty mapping. Values are returned as parsed, with no further type
    conversion.

    Raises:
        ConfigError: if *path* cannot be read, its contents are not
            valid YAML/JSON, the top level is not a mapping, or a key is
            not a string, or not one of :data:`CONFIG_KEYS`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file: {exc.strerror}") from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        reason = str(exc).splitlines()[0]
        raise ConfigError(f"{path}: invalid YAML/JSON: {reason}") from exc

    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ConfigError(f"{path}: top level must be a mapping of option names to values")

    unknown = sorted(str(key) for key in document if key not in CONFIG_KEYS)
    if unknown:
        allowed = ", ".join(sorted(CONFIG_KEYS))
        raise ConfigError(
            f"{path}: unknown config key(s): {', '.join(unknown)} (allowed keys: {allowed})"
        )

    return dict(document)
