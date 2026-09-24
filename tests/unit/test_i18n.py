"""Tests for message translation: language choice, localized errors, and the catalogs."""

from __future__ import annotations

import ast
import gettext
import io
import string
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from crawl4tools.i18n import (
    DOMAIN,
    ENGLISH,
    N_,
    SUPPORTED_LANGUAGES,
    Localized,
    LocalizedError,
    Translator,
    format_message,
    get_translator,
    language_from_argv,
    locale_language,
    render_exception,
    resolve_language,
)

SOURCE_DIR = Path(__file__).resolve().parents[2] / "src" / "crawl4tools"
CATALOG_LANGUAGES = [lang for lang in SUPPORTED_LANGUAGES if lang != "en"]
REFRESH_HINT = "run tools/i18n.sh update, translate, then tools/i18n.sh compile"


class FakeTranslator(gettext.NullTranslations):
    """A translator backed by a dict; unknown messages stay as they are."""

    def __init__(self, messages: dict[str, str]) -> None:
        super().__init__()
        self._messages = messages

    def gettext(self, message: str) -> str:
        return self._messages.get(message, message)


def po_path(lang: str) -> Path:
    return SOURCE_DIR / "locale" / lang / "LC_MESSAGES" / f"{DOMAIN}.po"


def load_catalog(lang: str) -> Catalog:
    with po_path(lang).open("rb") as f:
        return read_po(f)


# --- N_ ------------------------------------------------------------------------


def test_n_returns_the_message_unchanged() -> None:
    assert N_("fetch failed: {url}") == "fetch failed: {url}"


# --- get_translator ------------------------------------------------------------


def test_english_translator_is_the_identity_translator() -> None:
    assert get_translator("en") is ENGLISH


@pytest.mark.parametrize("lang", CATALOG_LANGUAGES)
def test_translator_loads_the_packaged_catalog(lang: str) -> None:
    assert isinstance(get_translator(lang), gettext.GNUTranslations)


def test_translator_for_a_language_without_catalog_keeps_english() -> None:
    t = get_translator("de")
    assert not isinstance(t, gettext.GNUTranslations)
    assert t.gettext("could not resolve host: {url}") == "could not resolve host: {url}"


def test_translator_is_cached_per_language() -> None:
    assert get_translator("ja") is get_translator("ja")


# --- locale_language -------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "ja_JP.UTF-8"}, "ja"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "ja"}, "ja"),
        ({"LANG": "ja.UTF-8"}, "ja"),
        ({"LANG": "ja_JP@custom"}, "ja"),
        ({"LANG": "JA_JP"}, "ja"),
        ({"LANG": "C"}, None),
        ({"LANG": "C.UTF-8"}, None),
        ({"LANG": "POSIX"}, None),
        ({"LANG": "de_DE.UTF-8"}, None),
        ({}, None),
        ({"LANGUAGE": "en", "LANG": "ja_JP.UTF-8"}, "en"),
        ({"LC_ALL": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}, "ja"),
        ({"LC_MESSAGES": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}, "ja"),
        ({"LC_ALL": "en_US.UTF-8", "LC_MESSAGES": "ja_JP.UTF-8"}, "en"),
        ({"LANGUAGE": "", "LANG": "ja_JP.UTF-8"}, "ja"),
        ({"LANGUAGE": "ja:en"}, "ja"),
        ({"LANGUAGE": "de:ja"}, None),
        ({"LC_ALL": "de_DE.UTF-8", "LANG": "ja_JP.UTF-8"}, None),
    ],
)
def test_locale_language(env: dict[str, str], expected: str | None) -> None:
    assert locale_language(env) == expected


# --- resolve_language --------------------------------------------------------------


def test_explicit_language_wins_over_variable_and_locale() -> None:
    env = {"X_LANG": "ja", "LANG": "ja_JP.UTF-8"}
    assert resolve_language("en", env=env, envvar="X_LANG", follow_locale=True) == "en"


def test_variable_wins_over_locale() -> None:
    env = {"X_LANG": "en", "LANG": "ja_JP.UTF-8"}
    assert resolve_language(None, env=env, envvar="X_LANG", follow_locale=True) == "en"


def test_variable_selects_japanese() -> None:
    env = {"X_LANG": "ja", "LANG": "en_US.UTF-8"}
    assert resolve_language(None, env=env, envvar="X_LANG", follow_locale=True) == "ja"


def test_locale_is_used_when_following_the_locale() -> None:
    env = {"LANG": "ja_JP.UTF-8"}
    assert resolve_language(None, env=env, envvar="X_LANG", follow_locale=True) == "ja"


def test_locale_is_ignored_when_not_following_the_locale() -> None:
    env = {"LANG": "ja_JP.UTF-8"}
    assert resolve_language(None, env=env, envvar="X_LANG", follow_locale=False) == "en"


def test_default_language_is_english() -> None:
    assert resolve_language(None, env={}, envvar="X_LANG", follow_locale=True) == "en"


def test_unsupported_explicit_language_is_skipped() -> None:
    env = {"LANG": "ja_JP.UTF-8"}
    assert resolve_language("de", env=env, envvar="X_LANG", follow_locale=True) == "ja"


def test_unsupported_variable_is_skipped() -> None:
    env = {"X_LANG": "de", "LANG": "ja_JP.UTF-8"}
    assert resolve_language(None, env=env, envvar="X_LANG", follow_locale=True) == "ja"


def test_empty_variable_is_skipped() -> None:
    env = {"X_LANG": "", "LANG": "ja_JP.UTF-8"}
    assert resolve_language(None, env=env, envvar="X_LANG", follow_locale=True) == "ja"


# --- language_from_argv ------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--lang", "ja"], "ja"),
        (["--lang=ja"], "ja"),
        (["https://example.com/", "--lang", "ja", "-o", "page.md"], "ja"),
        ([], None),
        (["https://example.com/"], None),
        (["--lang", "ja", "--lang", "en"], "en"),
        (["--lang=en", "--lang", "ja"], "ja"),
        (["--lang", "de"], None),
        (["--lang", "ja", "--lang", "de"], None),
        (["--", "--lang", "ja"], None),
        (["--lang", "ja", "--", "--lang", "en"], "ja"),
        (["--lang"], None),
        (["--lang", "ja", "--lang"], "ja"),
        (["--language", "ja"], None),
        (["--lang-x=ja"], None),
    ],
)
def test_language_from_argv(argv: list[str], expected: str | None) -> None:
    assert language_from_argv(argv) == expected


# --- LocalizedError and render_exception -------------------------------------------

TOO_MANY = "too many URLs: {count} given, at most {limit} per call"
TOO_MANY_JA = "URL が多すぎます: {count} 件(1 回あたり最大 {limit} 件)"


def test_localized_error_str_is_the_english_message() -> None:
    exc = LocalizedError(TOO_MANY, count=12, limit=10)
    assert str(exc) == "too many URLs: 12 given, at most 10 per call"


def test_localized_error_keeps_template_and_params() -> None:
    exc = LocalizedError(TOO_MANY, count=12, limit=10)
    assert exc.template == TOO_MANY
    assert exc.params == {"count": 12, "limit": 10}


def test_localized_error_renders_with_the_translated_template() -> None:
    exc = LocalizedError(TOO_MANY, count=12, limit=10)
    t = FakeTranslator({TOO_MANY: TOO_MANY_JA})
    assert exc.render(t) == "URL が多すぎます: 12 件(1 回あたり最大 10 件)"


def test_localized_error_renders_english_with_the_english_translator() -> None:
    exc = LocalizedError(TOO_MANY, count=12, limit=10)
    assert exc.render(ENGLISH) == "too many URLs: 12 given, at most 10 per call"


def test_localized_error_applies_format_specs() -> None:
    exc = LocalizedError("timed out after {timeout_s:g}s: {url}", timeout_s=30.0, url="u")
    assert str(exc) == "timed out after 30s: u"


def test_localized_error_without_params() -> None:
    exc = LocalizedError("at least one URL is required")
    assert str(exc) == "at least one URL is required"
    assert exc.params == {}


def test_localized_error_is_localized() -> None:
    assert isinstance(LocalizedError("x"), Localized)


class _ValueProblem(LocalizedError, ValueError):
    pass


class _ListenProblem(LocalizedError, OSError):
    pass


def test_localized_value_error_is_caught_as_value_error() -> None:
    with pytest.raises(ValueError) as info:
        raise _ValueProblem("directory must be a relative path: {directory}", directory="/x")
    assert str(info.value) == "directory must be a relative path: /x"


def test_localized_os_error_keeps_the_english_message() -> None:
    exc = _ListenProblem("cannot listen on {address}: {reason}", address="h:1", reason="busy")
    assert isinstance(exc, OSError)
    assert str(exc) == "cannot listen on h:1: busy"


def test_render_exception_translates_a_localized_error() -> None:
    exc = LocalizedError(TOO_MANY, count=12, limit=10)
    t = FakeTranslator({TOO_MANY: TOO_MANY_JA})
    assert render_exception(exc, t) == "URL が多すぎます: 12 件(1 回あたり最大 10 件)"


def test_render_exception_uses_render_of_any_localized_exception() -> None:
    class Custom(Exception):
        def render(self, t: Translator) -> str:
            return t.gettext("could not resolve host")

    t = FakeTranslator({"could not resolve host": "ホスト名を解決できません"})
    assert render_exception(Custom("english"), t) == "ホスト名を解決できません"


def test_render_exception_shows_other_exceptions_as_they_are() -> None:
    t = FakeTranslator({"boom": "どかん"})
    assert render_exception(ValueError("boom"), t) == "boom"
    assert render_exception(OSError(13, "Permission denied"), t) == "[Errno 13] Permission denied"


def test_format_message_translates_the_template_and_fills_params() -> None:
    t = FakeTranslator({TOO_MANY: TOO_MANY_JA})
    message = format_message(t, TOO_MANY, {"count": 12, "limit": 10})
    assert message == "URL が多すぎます: 12 件(1 回あたり最大 10 件)"


def test_format_message_keeps_format_specs() -> None:
    message = format_message(ENGLISH, "timed out after {timeout_s:g}s", {"timeout_s": 2.5})
    assert message == "timed out after 2.5s"


def test_format_message_renders_localized_params_in_the_same_language() -> None:
    inner = LocalizedError("could not resolve host: {url}", url="https://a.invalid/")
    t = FakeTranslator(
        {
            "proxy failed ({error})": "プロキシに失敗しました({error})",
            "could not resolve host: {url}": "ホスト名を解決できません: {url}",
        }
    )
    message = format_message(t, "proxy failed ({error})", {"error": inner})
    assert message == "プロキシに失敗しました(ホスト名を解決できません: https://a.invalid/)"


def test_localized_error_renders_localized_params_in_the_same_language() -> None:
    inner = LocalizedError("could not resolve host: {url}", url="https://a.invalid/")
    outer = LocalizedError("proxy failed ({error})", error=inner)
    t = FakeTranslator({"could not resolve host: {url}": "ホスト名を解決できません: {url}"})
    assert str(outer) == "proxy failed (could not resolve host: https://a.invalid/)"
    assert outer.render(t) == "proxy failed (ホスト名を解決できません: https://a.invalid/)"


# --- catalogs --------------------------------------------------------------------


def _field_names(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}


@pytest.mark.parametrize("lang", CATALOG_LANGUAGES)
def test_catalog_has_exactly_the_extracted_messages(lang: str) -> None:
    extracted = {message for _, _, message, _, _ in extract_from_dir(str(SOURCE_DIR))}
    catalog = load_catalog(lang)
    in_catalog = {message.id for message in catalog if message.id}
    assert sorted(map(str, extracted - in_catalog)) == [], f"missing from {lang}.po: {REFRESH_HINT}"
    assert sorted(map(str, in_catalog - extracted)) == [], f"stale in {lang}.po: {REFRESH_HINT}"
    assert list(catalog.obsolete) == [], f"obsolete entries in {lang}.po: {REFRESH_HINT}"


@pytest.mark.parametrize("lang", CATALOG_LANGUAGES)
def test_catalog_messages_are_translated(lang: str) -> None:
    catalog = load_catalog(lang)
    assert not catalog.fuzzy, f"the header of {lang}.po is marked fuzzy"
    untranslated = [message.id for message in catalog if message.id and not message.string]
    fuzzy = [message.id for message in catalog if message.id and message.fuzzy]
    assert untranslated == [], f"untranslated in {lang}.po"
    assert fuzzy == [], f"fuzzy in {lang}.po (review and remove the flag)"


@pytest.mark.parametrize("lang", CATALOG_LANGUAGES)
def test_catalog_translations_keep_the_placeholders(lang: str) -> None:
    mismatched = [
        (message.id, message.string)
        for message in load_catalog(lang)
        if message.id
        and message.string
        and _field_names(str(message.id)) != _field_names(str(message.string))
    ]
    assert mismatched == []


@pytest.mark.parametrize("lang", CATALOG_LANGUAGES)
def test_compiled_catalog_is_up_to_date(lang: str) -> None:
    compiled = io.BytesIO()
    write_mo(compiled, load_catalog(lang))
    mo_file = po_path(lang).with_suffix(".mo")
    assert compiled.getvalue() == mo_file.read_bytes(), (
        f"{mo_file.name} is stale: run tools/i18n.sh compile"
    )


@pytest.mark.parametrize("lang", CATALOG_LANGUAGES)
def test_translator_returns_every_catalog_translation(lang: str) -> None:
    t = get_translator(lang)
    wrong = [
        message.id
        for message in load_catalog(lang)
        if message.id and message.string and t.gettext(str(message.id)) != message.string
    ]
    assert wrong == []


def test_source_does_not_import_babel() -> None:
    # Babel is a development tool only; the package must run without it.
    offenders = []
    for path in sorted(SOURCE_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "babel" or name.startswith("babel.") for name in names):
                offenders.append(f"{path.relative_to(SOURCE_DIR)}:{node.lineno}")
    assert offenders == []


_MESSAGE_FUNCTIONS = {"gettext", "N_", "_"}


def _formatted_message_calls(source: str) -> list[int]:
    """Return the lines where a message is formatted before it is looked up.

    ``t.gettext(f"...")``, ``t.gettext("...".format(...))`` and
    ``t.gettext("..." % x)`` look up the formatted text, which is never in
    the catalog, so the message silently stays in English. ruff's INT rules
    only see bare function names, not ``t.gettext``.
    """
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in _MESSAGE_FUNCTIONS:
            continue
        message = node.args[0]
        formatted_call = (
            isinstance(message, ast.Call)
            and isinstance(message.func, ast.Attribute)
            and message.func.attr == "format"
        )
        if isinstance(message, ast.JoinedStr | ast.BinOp) or formatted_call:
            lines.append(node.lineno)
    return lines


def test_messages_are_not_formatted_before_lookup() -> None:
    offenders = [
        f"{path.relative_to(SOURCE_DIR)}:{lineno}"
        for path in sorted(SOURCE_DIR.rglob("*.py"))
        for lineno in _formatted_message_calls(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "format after the lookup: t.gettext('... {name}').format(name=...)"
