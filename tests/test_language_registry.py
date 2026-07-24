"""The registry is the fail-closed guarantee, so its own invariants are asserted.

`resolve()` returning None is what stops a claim about a language we cannot read
from reaching a checker whose silence would be read as evidence. Everything here
protects that: that an unknown extension resolves to nothing, that a backend cannot
be reached with an extension it never claimed, that two backends cannot both claim
one extension (which would make a verdict's provenance depend on import order), and
that the CLI's claim-type list is derived from the registry rather than restated
beside it.
"""

import pytest

from cca_checks import __main__ as cli
from cca_checks import languages
from cca_checks.languages import LanguageBackend, PythonBackend


def test_python_resolves_to_the_python_backend():
    backend = languages.resolve("svc.py")
    assert backend is not None
    assert backend.name == "python"


def test_rust_resolves_to_the_rust_backend():
    backend = languages.resolve("main.rs")
    assert backend is not None
    assert backend.name == "rust"


@pytest.mark.parametrize("path", [
    "app.ts", "main.go", "Lib.java", "x.rb", "Makefile", "README", ".env",
])
def test_an_uncovered_path_resolves_to_nothing(path):
    """None is the escalate signal. It must never quietly mean "assume Python"."""
    assert languages.resolve(path) is None


def test_extension_matching_is_case_insensitive():
    """A Windows checkout can hand us HANDLER.PY, and escalating on it would drop
    deterministic coverage for a file we can read perfectly well."""
    assert languages.resolve("HANDLER.PY") is languages.resolve("handler.py")


@pytest.mark.parametrize("path,expected", [
    ("a/b/c.py", ".py"),
    ("c.PY", ".py"),
    ("archive.tar.gz", ".gz"),
    ("Makefile", ""),
    (".gitignore", ".gitignore"),
    ("", ""),
])
def test_extension_of(path, expected):
    assert languages.extension_of(path) == expected


def test_every_backend_satisfies_the_protocol():
    for backend in languages.BACKENDS:
        assert isinstance(backend, LanguageBackend), backend


def test_every_backend_declares_at_least_one_extension_and_claim_type():
    """A backend claiming nothing is unreachable, which is a silent self-disable --
    the registry would resolve every file to None and the whole deterministic layer
    would go dark with no error anywhere."""
    for backend in languages.BACKENDS:
        assert backend.extensions, backend.name
        assert backend.claim_types, backend.name


def test_extensions_are_normalised():
    """`resolve` lower-cases and dot-prefixes before lookup, so a backend declaring
    "py" or ".PY" would simply never match and would fail as silent under-coverage."""
    for backend in languages.BACKENDS:
        for ext in backend.extensions:
            assert ext.startswith("."), (backend.name, ext)
            assert ext == ext.lower(), (backend.name, ext)


def test_no_two_backends_claim_the_same_extension():
    """Which backend settles a claim must not depend on import order."""
    seen: dict[str, str] = {}
    for backend in languages.BACKENDS:
        for ext in backend.extensions:
            assert ext not in seen, f"{ext} claimed by both {seen[ext]} and {backend.name}"
            seen[ext] = backend.name


def test_a_duplicate_extension_is_rejected_at_registration():
    """The invariant above is enforced, not merely true today."""
    class Impostor(PythonBackend):
        name = "impostor"

    with pytest.raises(ValueError, match="both claim"):
        languages._index((PythonBackend(), Impostor()))


def test_a_malformed_extension_is_rejected_at_registration():
    class Shouty(PythonBackend):
        name = "shouty"
        extensions = frozenset({".PY"})

    with pytest.raises(ValueError, match="lower-case"):
        languages._index((Shouty(),))


def test_an_incomplete_backend_is_rejected_at_registration():
    """A missing method must fail at import, not in the middle of an audit."""
    class Stub:
        name = "stub"
        extensions = frozenset({".stub"})
        claim_types = frozenset({"type"})
        # no enclosing_span, no settle

    with pytest.raises(TypeError, match="LanguageBackend"):
        languages._index((Stub(),))


def test_cli_claim_types_are_derived_from_the_registry():
    """Restating the list beside the registry is how `--claim-type panic_path` exits
    with an argparse usage error on a repo whose backend settles it -- and a usage
    error renders no verdict at all, so the finding leaves the pipeline unevidenced."""
    assert set(cli.CLAIM_TYPES) == {
        ct for backend in languages.BACKENDS for ct in backend.claim_types
    }


def test_supported_extensions_matches_the_backends():
    assert languages.supported_extensions() == {
        ext for backend in languages.BACKENDS for ext in backend.extensions
    }
