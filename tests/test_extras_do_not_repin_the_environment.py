"""An install extra must not rewrite packages that have nothing to do with auditing.

WHAT HAPPENED, 2026-08-31. `pip install 'cca-audit[verify]'` downgraded a user's
`mcp` from 2.1.0 to 1.29.0 and broke unrelated MCP tooling. Nothing in this project
imports `mcp`. The chain was: `verify` listed `semgrep`, semgrep hard-pins
`mcp==1.29.0` (also `ruamel.yaml.clib==0.2.15` and `pywin32==311`), and pip
faithfully enforced it across the whole environment.

The part that makes it a bug rather than bad luck: `cca_checks` never imports
semgrep. `semgrep_check.py` resolves it on PATH and spawns it, exactly like `cargo`,
which nobody would think to pip-install. It sat in the extra because "one install"
read as tidy, and it bought nothing that `pipx install semgrep` does not.

These tests pin the fix so a future tidy-up cannot quietly undo it.
"""

from __future__ import annotations

import pathlib
import re

try:  # tomllib is 3.11+, and requires-python here is >=3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - taken on the 3.10 CI job
    import tomli as tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXTRAS = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
EXTRAS = EXTRAS["project"]["optional-dependencies"]

#: Extras a user installs into their own working environment. `taint` is excluded
#: on purpose: it exists so that someone who WANTS semgrep in-environment can opt
#: into its pins knowingly, which is a different act from discovering them.
USER_FACING = ("verify", "numeric", "rust", "dev")

#: Distributions that pin unrelated packages to an exact version, so installing
#: them into a shared environment rewrites things the audit layer never touches.
#: Add to this list only with the transitive pin named, never on suspicion.
PINS_UNRELATED_PACKAGES = {
    "semgrep": "mcp==1.29.0, ruamel.yaml.clib==0.2.15, pywin32==311",
}


def _names(extra: str) -> set[str]:
    """Distribution names in `extra`, without version specifiers or markers."""
    return {
        re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0].strip().lower()
        for spec in EXTRAS[extra]
    }


def test_no_user_facing_extra_installs_a_distribution_that_repins_the_environment():
    for extra in USER_FACING:
        clashing = _names(extra) & set(PINS_UNRELATED_PACKAGES)
        assert not clashing, (
            f"the `{extra}` extra installs {sorted(clashing)}, which pins "
            f"{'; '.join(PINS_UNRELATED_PACKAGES[c] for c in sorted(clashing))}. "
            "That rewrites packages unrelated to auditing in whatever environment "
            "the user installs into. Offer it as its own opt-in extra instead, and "
            "recommend pipx."
        )


def test_semgrep_is_still_offered_just_not_by_default():
    """Removing it outright would be the opposite mistake.

    The point is not that semgrep is unwelcome; it is that its pins must be chosen
    rather than inherited. An extra that no longer exists cannot be chosen.
    """
    assert "taint" in EXTRAS, "the opt-in path for semgrep must remain"
    assert "semgrep" in _names("taint")


def test_a_spawned_tool_is_only_ever_a_convenience_never_an_import():
    """The premise the whole fix rests on, asserted rather than remembered.

    If some future change actually imports semgrep as a library, it becomes a real
    dependency and this reasoning collapses. Better to fail here than to keep
    recommending pipx for something the code now needs in-process.
    """
    imports = re.compile(r"^\s*(?:import|from)\s+semgrep\b", re.M)
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "cca_checks").rglob("*.py")
        if imports.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"{offenders} import semgrep. It is spawned via resolve_tool(), not "
        "imported; if that has changed, the packaging decision needs revisiting."
    )


def test_the_verify_extra_still_covers_everything_it_claims():
    """`verify` minus taint must still be a complete deterministic layer.

    Dropping semgrep is only safe because the gap is now REPORTED (capabilities
    probes by execution). Silently shrinking `verify` further would recreate the
    "extra that half-enables a feature" failure pyproject.toml warns about.
    """
    verify = _names("verify")
    for required in ("hypothesis", "pytest", "pyright", "mpmath",
                     "tree-sitter", "tree-sitter-rust"):
        assert required in verify, f"`verify` lost {required}"
