import re
from pathlib import Path

import pytest

from cca_checks import languages

RULES = Path(__file__).resolve().parent.parent / "cca_checks" / "rules"
CLASSES = ["sql", "command", "code_exec", "path"]

# Derived from the registry, not restated. A backend that ships a catalog is a
# backend whose refutations rest on it, so it must be held to the same two-tier
# contract -- and a hand-maintained list here would let the next language's catalog
# arrive untested, which is exactly when a missing sink turns real findings into
# confident refutations.
LANGUAGES = sorted(
    b.name for b in languages.BACKENDS if hasattr(b, "semgrep_catalog")
)


def catalog(language: str, kind: str) -> str:
    backend = next(b for b in languages.BACKENDS if b.name == language)
    return backend.semgrep_catalog(kind)


def read(name):
    return (RULES / name).read_text(encoding="utf-8")


def has_exact_rule_id(catalog_text, rule_id):
    """True iff `catalog_text` contains a YAML rule-list entry whose id is
    EXACTLY `rule_id` — not merely prefixed by it.

    A plain substring check (`f"id: {rule_id}" in text`) is satisfied by any
    id sharing that prefix, e.g. `sink-strict-sql-v2` would satisfy a check
    for `sink-strict-sql`. Since the sink-class name is derived from the rule
    id at runtime (Task 3), a suffixed rename would pass a substring-based
    catalog test but silently break — or silently misclassify — at runtime.

    This helper anchors the match to a full line (`- id: <rule_id>`, allowing
    leading whitespace and requiring the line end immediately after the id)
    so a suffix like `-v2` makes the match fail, as it should.
    """
    pattern = r"^[ \t]*-\s*id:\s*" + re.escape(rule_id) + r"[ \t]*$"
    return re.search(pattern, catalog_text, re.MULTILINE) is not None


@pytest.mark.parametrize("language", LANGUAGES)
def test_rule_files_exist(language):
    assert (RULES / catalog(language, "sinks")).is_file()
    assert (RULES / catalog(language, "taint")).is_file()


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("cls", CLASSES)
def test_every_class_has_both_tiers(cls, language):
    sinks = read(catalog(language, "sinks"))
    assert has_exact_rule_id(sinks, f"sink-strict-{cls}")
    assert has_exact_rule_id(sinks, f"sink-loose-{cls}")


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("cls", CLASSES)
def test_every_class_has_a_taint_rule(cls, language):
    assert has_exact_rule_id(read(catalog(language, "taint")), f"taint-{cls}")


@pytest.mark.parametrize("language", LANGUAGES)
def test_sinks_file_declares_no_taint_rules(language):
    # The sink catalog answers "does a sink occur here", nothing more. A taint rule
    # here would silently turn a presence check into a dataflow claim.
    assert "mode: taint" not in read(catalog(language, "sinks"))


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_rule_declares_its_own_language(language):
    """A catalog scanned against the wrong language matches nothing, and nothing is
    what licenses a FALSE_POSITIVE. A rule left with the language it was copied from
    would be silently inert."""
    text = read(catalog(language, "sinks")) + read(catalog(language, "taint"))
    declared = set(re.findall(r"^\s*languages:\s*\[([^\]]*)\]", text, re.MULTILINE))
    assert declared, f"{language}: no rule declares a language"
    for entry in declared:
        assert language in entry, f"{language} catalog declares languages: [{entry}]"


def test_suffixed_id_would_not_satisfy_the_catalog():
    # Proves the tightening against the real catalog: a would-be suffixed
    # rename of a real id (e.g. the literal "sink-strict-sql-v2") must not be
    # present, and must not be mistaken for the real id either.
    sinks = read("python_sinks.yaml")
    assert "sink-strict-sql-v2" not in sinks
    assert not has_exact_rule_id(sinks, "sink-strict-sql-v2")


def test_has_exact_rule_id_helper_rejects_suffixed_id():
    # Unit-test the helper itself (independent of any real catalog file) against
    # a synthetic snippet containing a suffixed id, proving the regex actually
    # discriminates `sink-strict-sql` from `sink-strict-sql-v2` rather than
    # happening to pass because the real catalog doesn't have the suffix.
    synthetic = "rules:\n  - id: sink-strict-sql-v2\n    languages: [python]\n"
    assert has_exact_rule_id(synthetic, "sink-strict-sql-v2") is True
    assert has_exact_rule_id(synthetic, "sink-strict-sql") is False
