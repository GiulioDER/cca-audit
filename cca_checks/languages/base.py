"""What a language must supply before the deterministic layer will speak about it.

THE CONTRACT, AND WHY IT IS SHAPED THIS WAY. A backend does not merely say "I can
read .rs files". It declares the claim types it can actually SETTLE, and the
registry refuses everything else. That asymmetry is the point: a checker's silence
is what licenses a FALSE_POSITIVE, so a language must never be able to reach a
checker that cannot read it -- pyright happily parses Rust as Python, finds no
`reportUndefinedVariable`, and refutes a real defect with an authoritative
`source: pyright`. See `cca_checks/__main__.py::_validate_language`.

`claim_types` is therefore a POSITIVE list, never a deny-list. A new claim type is
unsupported by every backend until its author opts that backend in, which is the
safe default: a backend that forgot to declare it escalates, rather than settling it
with a checker built for a different language.
"""

from typing import Protocol, runtime_checkable

from ..claim import Claim, Verdict


@runtime_checkable
class LanguageBackend(Protocol):
    """One language's deterministic settlers.

    `runtime_checkable` so the registry can assert conformance at import time rather
    than discovering a missing method the first time a claim routes to it -- which,
    in a checker, would surface as an exception in the middle of an audit.
    """

    #: Short identifier used in evidence strings and the `capabilities` output.
    name: str

    #: Lower-case file extensions, dot included: `{".py"}`, `{".rs"}`.
    extensions: frozenset[str]

    #: Claim types this backend can settle. Anything outside it escalates.
    claim_types: frozenset[str]

    def enclosing_span(self, path: str, line_1based: int) -> tuple[int, int]:
        """1-indexed inclusive line span of the innermost function containing the line.

        Falls back to the whole file when the line sits outside any function. Raises
        when the file cannot be parsed -- callers already treat an exception here as
        "scope unknown" and escalate, which is the correct reading: a span we could
        not compute must not be silently widened to the whole file, because a
        whole-file span makes a refutation rest on silence across code the claim
        never referred to.
        """
        ...

    def settle(self, claim: Claim) -> Verdict:
        """Render a verdict for a claim this backend declared it can settle.

        The registry guarantees `claim.claim_type in self.claim_types` before this is
        called, so an implementation may treat an unknown claim type as a programming
        error rather than a runtime condition to escalate.
        """
        ...
