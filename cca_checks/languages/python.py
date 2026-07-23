"""The Python backend: the settlers that were the whole deterministic layer.

Nothing here is new logic. `settle` is the dispatch that lived in
`cca_checks/__main__.py::_check`, and `enclosing_span` is `cca_checks/scope.py`,
both moved behind the protocol unchanged. Keeping the move mechanical is deliberate:
the existing suite is the proof that introducing the layer changed no behaviour, and
that proof is only worth something if the code under it did not change at the same
time.

The imports are module-scope and bare (`run_pyright`, not `pyright_check.run_pyright`)
so this module is the patch seam for tests that need to stand a checker down. That
seam used to be `__main__`; it moved here with the dispatch it belongs to.
"""

from ..claim import Claim, Verdict
from ..clock_check import verdict_for_clock_leak
from ..pyright_check import RULES_BY_CLAIM, run_pyright, verdict_for_claim
from ..scope import python_enclosing_span
from ..semgrep_check import verdict_for_taint
from ..toolpath import resolve_tool

#: Claim types this backend settles with a real tool. `crash_impact` and `numeric`
#: are absent on purpose: they arrive through the `repro` / `numeric` subcommands,
#: which take a generated test file rather than a claim coordinate, so they are not
#: routed by extension and `settle` never sees them.
CLAIM_TYPES = frozenset(RULES_BY_CLAIM) | {"taint", "clock_leak"}


class PythonBackend:
    name = "python"
    extensions = frozenset({".py"})
    claim_types = CLAIM_TYPES

    def enclosing_span(self, path: str, line_1based: int) -> tuple[int, int]:
        return python_enclosing_span(path, line_1based)

    def semgrep_catalog(self, kind: str) -> str:
        """Bundled rule file for `kind` in ("sinks", "taint")."""
        return f"python_{kind}.yaml"

    def unavailable_claim_types(self) -> dict[str, str]:
        """Claim types whose tool is missing on THIS machine, with the reason.

        Reported rather than subtracted from `claim_types`: an agent that sees
        `taint` listed as unavailable knows to escalate it, whereas one that never
        sees it cannot tell that from a claim type nobody supports.
        """
        out = {}
        if resolve_tool("pyright") is None:
            for claim_type in RULES_BY_CLAIM:
                out[claim_type] = "pyright is not on PATH"
        if resolve_tool("semgrep") is None:
            out["taint"] = "semgrep is not on PATH"
        return out

    def settle(self, claim: Claim) -> Verdict:
        if claim.claim_type == "taint":
            return verdict_for_taint(claim)
        if claim.claim_type == "clock_leak":
            return verdict_for_clock_leak(claim)
        return verdict_for_claim(claim, run_pyright(claim.file),
                                 RULES_BY_CLAIM[claim.claim_type])
