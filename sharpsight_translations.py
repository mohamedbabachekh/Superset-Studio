"""Superset in French (owner, 2026-09-26): the translations the release image ships, compiled at image build.

    python sharpsight_translations.py [locale ...]        (default: fr — run by the Dockerfile, as root)

WHY A STEP OF OUR OWN. apache/superset is published as a LEAN build (BUILD_TRANSLATIONS=false): each locale ships its
source catalogue `messages.po` and nothing else. Superset reads two compiled forms of it, and without them setting
BABEL_DEFAULT_LOCALE changes nothing on the page:

  * `messages.mo` — the server's strings (menus, errors), read by Flask-Babel;
  * `messages.json` — the browser's (every button, filter panel and table control), served as the « language pack »
    (superset/translations/utils.py) in the Jed 1.x shape its frontend reads.

The official build makes the JSON with the npm tool po2json; the image has no Node, and Babel — which Superset
already carries — reads the same catalogue.

FUZZY ENTRIES ARE KEPT WHEN THEIR PLACEHOLDERS MATCH. Superset 6.1's French catalogue marks 1 633 of 4 597 strings
« fuzzy » (unconfirmed); leaving them out, as po2json does, gave 2 879 strings — a page half in English, which is
what the owner asked to end. Most are right (« Formatage conditionnel », « Tout réduire »); a few are not, and a
fuzzy string whose %s / %d / %(name)s / {name} differ from the English could break the sentence it formats, so
those are dropped. OVERRIDES, last, are SharpSight's own reading of the strings a reader of our dashboards sees —
the ones a review found in English or wrong (2026-09-26); add to them rather than editing the catalogue.
"""
from __future__ import annotations

import json
import os
import re
import sys

DOMAIN = "superset"
_PLACEHOLDER = re.compile(r"%\([^)]+\)[sdif]|%[sdif]|\{[^{}]*\}")

OVERRIDES = {
    "fr": {
        "Filters and controls": "Filtres et contrôles",      # the catalogue's fuzzy « Contrôles supplémentaires »
        "Search by": "Rechercher par",
        "entries per page": "entrées par page",
        "Draft": "Brouillon",                                 # the catalogue says « VERSION PRÉLIMINAIRE »
        "No results were returned for this query": "Aucun résultat pour cette requête",
        "View as table": "Voir sous forme de tableau",
        "Inverse selection": "Inverser la sélection",
    },
}


def _forms(message) -> list[str]:
    return [str(f) for f in message.string] if isinstance(message.string, (list, tuple)) else [str(message.string)]


def _source(message) -> str:
    return message.id[0] if isinstance(message.id, (list, tuple)) else message.id


def same_placeholders(source: str, forms: list[str]) -> bool:
    wanted = sorted(_PLACEHOLDER.findall(source))
    return all(sorted(_PLACEHOLDER.findall(form)) == wanted for form in forms if form)


def usable(message) -> bool:
    """A translated entry, confirmed — or fuzzy with exactly the English placeholders."""
    if not message.id or not any(_forms(message)):
        return False
    return not message.fuzzy or same_placeholders(_source(message), _forms(message))


def language_pack(catalog, locale: str) -> dict:
    """The Jed 1.x document the frontend's Translator is configured with."""
    data = {"": {"domain": DOMAIN, "plural_forms": catalog.plural_forms, "lang": locale}}
    for message in catalog:
        if not usable(message):
            continue
        key = _source(message)                  # a plural is keyed by its singular, every form listed
        if message.context:
            key = f"{message.context}\u0004{key}"
        data[key] = _forms(message)
    for key, text in OVERRIDES.get(locale, {}).items():
        data[key] = [text]
    return {"domain": DOMAIN, "locale_data": {DOMAIN: data}}


def compile_locale(root: str, locale: str) -> tuple[int, int]:
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po

    folder = os.path.join(root, locale, "LC_MESSAGES")
    with open(os.path.join(folder, "messages.po"), "rb") as source:
        catalog = read_po(source, locale=locale, domain=DOMAIN)
    pack = language_pack(catalog, locale)
    # The server's strings follow the same rule as the browser's: a usable fuzzy entry is confirmed, a broken one
    # is emptied (an empty translation falls back to the English), and the overrides win.
    for message in catalog:
        if message.id and message.fuzzy:
            if usable(message):
                message.flags.discard("fuzzy")
            else:
                message.string = ("",) * len(message.string) if isinstance(message.string, tuple) else ""
    for key, text in OVERRIDES.get(locale, {}).items():
        catalog.add(key, text)
    with open(os.path.join(folder, "messages.mo"), "wb") as target:
        write_mo(target, catalog, use_fuzzy=False)
    with open(os.path.join(folder, "messages.json"), "w", encoding="utf-8") as target:
        json.dump(pack, target, ensure_ascii=False)
    return len(catalog), len(pack["locale_data"][DOMAIN]) - 1


if __name__ == "__main__":
    # The installed package's folder, found WITHOUT importing it: at image build there is no configuration to load,
    # and importing `superset` would start reading one.
    import importlib.util
    root = os.path.join(importlib.util.find_spec("superset").submodule_search_locations[0], "translations")
    for locale in sys.argv[1:] or ["fr"]:
        total, translated = compile_locale(root, locale)
        print(f"sharpsight-translations: {locale}: {translated} of {total} strings translated, .mo and .json written")
        if translated < total // 2:
            raise SystemExit(f"{locale}: fewer than half the strings translated — refusing to ship a half-French UI")
