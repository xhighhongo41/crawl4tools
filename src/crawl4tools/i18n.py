"""Message translation for the three commands.

Messages are written in English and passed through a :data:`Translator`
(``t.gettext("...")``) at the point where they are shown to a user. The
translator is passed explicitly as an argument; there is no global ``_``.
Logs always use :data:`ENGLISH`.

The Japanese catalog lives in ``locale/ja/LC_MESSAGES/crawl4tools.po`` and
is compiled to the ``.mo`` file next to it, which ships with the package, so
nothing beyond the standard library is needed at runtime. ``tools/i18n.sh``
updates and compiles the catalog during development.

This module must not import the engine or the server packages: they depend
on it, not the other way round.
"""

from __future__ import annotations

import functools
import gettext
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypeAlias, runtime_checkable

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ja")
DEFAULT_LANGUAGE = "en"
DOMAIN = "crawl4tools"
LOCALE_DIR = Path(__file__).parent / "locale"

# Environment variables that name the user's locale, in the order the
# standard ``gettext`` module consults them.
LOCALE_ENV_VARS: tuple[str, ...] = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")

Translator: TypeAlias = gettext.NullTranslations
"""Anything with ``gettext(message) -> str``; the English one returns its input."""

ENGLISH: Translator = gettext.NullTranslations()
"""The identity translator: messages stay in English (logs, defaults)."""


def N_(message: str) -> str:
    """Mark ``message`` for extraction without translating it.

    Used for message templates that are stored (class attributes, exception
    templates) and translated later with ``t.gettext(template)``.
    Postcondition: returns ``message`` unchanged.
    """
    return message


@functools.cache
def get_translator(lang: str) -> Translator:
    """Return the translator for ``lang`` (cached per language).

    ``"en"`` gives :data:`ENGLISH`. Another supported language gives the
    catalog compiled into the package. A language without a catalog falls
    back to an English (identity) translator instead of raising.
    """
    if lang == DEFAULT_LANGUAGE:
        return ENGLISH
    return gettext.translation(DOMAIN, LOCALE_DIR, languages=[lang], fallback=True)


def locale_language(env: Mapping[str, str]) -> str | None:
    """Return the supported language named by the locale variables in ``env``.

    Looks at the first non-empty variable of :data:`LOCALE_ENV_VARS`, takes
    the part before the first ``:``, then the language code before ``_``,
    ``.`` or ``@``, lowercased. Returns it when it is in
    :data:`SUPPORTED_LANGUAGES`, otherwise ``None`` (``C``, ``POSIX``,
    unsupported languages, or no variable set).
    """
    value = next((env[name] for name in LOCALE_ENV_VARS if env.get(name)), "")
    code = value.split(":", 1)[0]
    for separator in ("_", ".", "@"):
        code = code.split(separator, 1)[0]
    code = code.lower()
    return code if code in SUPPORTED_LANGUAGES else None


def resolve_language(
    explicit: str | None, *, env: Mapping[str, str], envvar: str, follow_locale: bool
) -> str:
    """Decide the message language.

    Order: ``explicit`` (the ``--lang`` value), then ``env[envvar]``, then
    (when ``follow_locale``) :func:`locale_language`, then
    :data:`DEFAULT_LANGUAGE`. A value that is not in
    :data:`SUPPORTED_LANGUAGES` is skipped; rejecting it is the job of the
    command-line parser. Postcondition: the result is always supported.
    """
    for candidate in (explicit, env.get(envvar)):
        if candidate in SUPPORTED_LANGUAGES:
            return candidate
    if follow_locale:
        from_locale = locale_language(env)
        if from_locale is not None:
            return from_locale
    return DEFAULT_LANGUAGE


def language_from_argv(argv: Sequence[str]) -> str | None:
    """Return the ``--lang`` value in ``argv`` before the command is built.

    Accepts ``--lang X`` and ``--lang=X``; the last one wins and nothing
    after ``--`` is looked at. Returns ``None`` when there is no ``--lang``
    or the winning value is not supported. A trailing ``--lang`` without a
    value is ignored.
    """
    found: str | None = None
    args = list(argv)
    for index, arg in enumerate(args):
        if arg == "--":
            break
        if arg == "--lang":
            if index + 1 < len(args):
                found = args[index + 1]
        elif arg.startswith("--lang="):
            found = arg.removeprefix("--lang=")
    return found if found in SUPPORTED_LANGUAGES else None


@runtime_checkable
class Localized(Protocol):
    """Something that can render itself as a message in a given language."""

    def render(self, t: Translator) -> str:
        """Return the message translated with ``t``."""
        ...


class LocalizedError(Exception):
    """An exception whose message can be shown in any supported language.

    ``template`` is an English ``str.format`` template marked with
    :func:`N_`; ``params`` fill its ``{name}`` fields. ``str(exc)`` is the
    English message (for logs and for callers that do not translate).
    Subclasses may also inherit from a built-in exception such as
    ``ValueError`` so existing ``except`` clauses keep working.
    """

    def __init__(self, template: str, /, **params: object) -> None:
        self.template = template
        self.params: dict[str, object] = params
        super().__init__(self.render(ENGLISH))

    def render(self, t: Translator) -> str:
        """Return the message with the template translated by ``t``."""
        return t.gettext(self.template).format(**self.params)


def render_exception(exc: BaseException, t: Translator) -> str:
    """Return the user-facing message of ``exc`` in the language of ``t``.

    Exceptions that implement :class:`Localized` are rendered with ``t``;
    any other exception (from the standard library or a dependency) is
    shown as ``str(exc)``.
    """
    if isinstance(exc, Localized):
        return exc.render(t)
    return str(exc)
