"""Extension -> backend resolution, resolved ONCE before any checker runs.

This module is the whole fail-closed guarantee. `resolve()` returning None is what
stops a claim about a language we cannot read from reaching a checker whose silence
would be read as evidence. Every caller maps None onto its existing "escalate" path,
so a new language is safe by default: it is UNCERTAIN everywhere until a backend
opts into it, and it is never quietly settled by a tool built for something else.

REGISTRATION IS EAGER AND ORDER-INDEPENDENT. Backends are constructed at import
time and indexed by extension. A second backend claiming an extension another
already owns is a hard error rather than a last-writer-wins silent override --
"which tool settled this claim" would otherwise depend on import order, and a
verdict whose provenance depends on import order is not evidence.
"""

from .base import LanguageBackend
from .python import PythonBackend
from .rust import RustBackend

#: Every backend the deterministic layer knows about. Adding a language means adding
#: it here and nowhere else -- see `_BY_EXTENSION` for what enforces that.
#:
#: A backend is registered even when its optional dependency is absent. That is
#: deliberate: an uninstalled tree-sitter grammar must escalate with "the grammar is
#: not installed", which tells the reader what to do, rather than make the whole
#: language read as unsupported -- indistinguishable from a language we never built.
BACKENDS: tuple[LanguageBackend, ...] = (
    PythonBackend(),
    RustBackend(),
)


def _index(backends: tuple[LanguageBackend, ...]) -> dict[str, LanguageBackend]:
    out: dict[str, LanguageBackend] = {}
    for backend in backends:
        if not isinstance(backend, LanguageBackend):
            raise TypeError(
                f"{type(backend).__name__} does not satisfy the LanguageBackend "
                f"protocol; a backend missing a method would fail mid-audit instead "
                f"of at import"
            )
        for ext in backend.extensions:
            if ext != ext.lower() or not ext.startswith("."):
                raise ValueError(
                    f"{backend.name} declares extension {ext!r}; extensions must be "
                    f"lower-case and dot-prefixed, because `resolve` lower-cases the "
                    f"path's extension before looking it up and would silently miss it"
                )
            if ext in out:
                raise ValueError(
                    f"{backend.name} and {out[ext].name} both claim {ext!r}; which one "
                    f"settles a claim would depend on import order"
                )
            out[ext] = backend
    return out


_BY_EXTENSION = _index(BACKENDS)


def extension_of(path: str) -> str:
    """Lower-cased extension including the dot, or "" when there is none.

    Deliberately not `os.path.splitext` at each call site: the lower-casing has to
    match `_index`'s validation exactly, and a call site that forgot it would fail to
    resolve `Handler.PY` and escalate a Python claim for no reason.
    """
    _, _, ext = path.rpartition(".")
    return f".{ext.lower()}" if ext and ext != path else ""


def resolve(path: str) -> LanguageBackend | None:
    """The backend covering `path`, or None when no backend covers it.

    None means "escalate". It never means "assume Python": assuming a language is
    exactly how a `.rs` file collected a `source: pyright` refutation.
    """
    return _BY_EXTENSION.get(extension_of(path))


def supported_extensions() -> frozenset[str]:
    """Every extension some backend covers. Used by the CLI's language guard."""
    return frozenset(_BY_EXTENSION)


__all__ = [
    "BACKENDS",
    "LanguageBackend",
    "PythonBackend",
    "RustBackend",
    "extension_of",
    "resolve",
    "supported_extensions",
]
