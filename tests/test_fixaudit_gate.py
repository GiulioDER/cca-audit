"""The gate owes L5.4 (fix audit) to runs started by the new version, and only to them."""

from __future__ import annotations

import pytest
from conftest import (
    append_ledger,
    findings_output,
    load_hook,
    plan_output,
    verdicts_output,
    write_output,
)

from cca_checks import pipeline_state as ps

core = load_hook("cca_gate_core.py")


def _state(tier, **extra):
    return {"schema": core.SCHEMA, "tier": tier, "mode": "DIFF", "no_fix": False,
            "layers": {}, **extra}


@pytest.mark.parametrize("tier", core.TIERS)
def test_a_new_run_owes_l54_after_l5_on_every_tier(tier):
    """Invariant: a fix_audit run must audit its fixes (L5.4) right after L5.

    Red 2026-11-22: baseline c3586f0 plus the names added without the table change;
    failed at `assert "L5.4" in required` (L5.4 absent from required_layers).
    """
    required = core.required_layers(_state(tier, fix_audit=True), owed=True)
    assert "L5.4" in required
    assert required.index("L5.4") == required.index("L5") + 1


@pytest.mark.parametrize("tier", core.TIERS)
def test_an_old_run_owes_exactly_what_it_owed_before(tier):
    """Invariant: a state without "fix_audit" keeps the pre-L5.4 tables byte for byte.

    Red 2026-11-22: mutation in cca_gate_core.required_layers, `state.get("fix_audit")`
    replaced by `True`; failed at `assert required == expected` (L5.4 appeared).
    """
    state = _state(tier)
    std = tier != "FAST"
    expected = ("L1", "L2", "L2.5") + (("L2.6",) if std else ()) + ("L3", "L4", "L5") \
        + (("L5.5", "L5.6") if std else ()) + ("L6",)
    assert core.required_layers(state, owed=True) == expected
    assert core.predecessors("L5.5", state, True) == ("L5",)
    assert core.predecessors("L5.6", state, True) == ("L5",)
    assert core.predecessors("L6", state, True) == (("L5", "L5.5", "L5.6") if std else ("L5",))


def _through_l3(repo, tier="STANDARD"):
    state = ps.start(tier, root=repo)
    ps.record("L1", detail="6 auditors", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 1}, output=findings_output(repo, 1), root=repo)
    append_ledger(repo, state.run_id, 1)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, confirmed=1), root=repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 1}, output=plan_output(repo, to_fix=1), root=repo)
    return state


def test_l3_stores_the_prefix_tree(git_repo):
    """Invariant: L3 stores the tree the run held when the plan was recorded.

    Red 2026-11-22: baseline c3586f0 plus the field added to PipelineState but never set
    in record(); failed at `assert state.prefix_tree == ...` (None != tree id).
    """
    _through_l3(git_repo)
    state = ps.load(git_repo)
    assert state.fix_audit is True
    assert state.prefix_tree == core.fingerprint(git_repo)


def test_l55_cannot_be_recorded_before_l54_in_a_new_run(git_repo):
    """Invariant: in a fix_audit run L5.5 has L5.4 as predecessor.

    Red 2026-11-22: mutation in cca_gate_core.predecessors, `"L5.5": ("L5.4",)` replaced
    by `("L5",)`; failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    _through_l3(git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(git_repo), root=git_repo)
    ps.record("L5", detail="green", root=git_repo)
    with pytest.raises(ValueError, match=r"L5\.4"):
        ps.record("L5.5", detail="all SAFE", root=git_repo)
