"""Leading-stem recovery (`recover_git_anchors --leading-stem`).

A fix that changed two or more production files, whose summary opens with the name stem of
exactly one changed source file, anchors that file at file level. Measured 2026-10-29 on 128
already-anchored multi-file rows: 121 correct (0.945, Wilson 95% 0.891 to 0.973).

Every red proof below was run on 2026-10-29 against the pre-change code or a named mutation of
`cca_checks/bugs/recover_git_anchors.py` or `cca_checks/bugs/anchor_log.py`, and is recorded in
the test's docstring.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from bugs_testkit import commit, make_repo, run_git
from bugs_testkit import isolated_git_environment as isolated_git_environment
from test_anchor_recovery import row
from test_git_anchor_recovery import _reader

from cca_checks.bugs import anchor_log, records, store


def _stem_history(tmp_path, *, summary="crypto_payment TOCTOU race (FOR UPDATE SKIP LOCKED)",
                  finding="BUG-1", changed=None, row_files=None):
    """A fix commit changing every path in `changed` (default: two production modules and a
    test), with no bullet in its message, so the bullet and single-file rules refuse it."""
    changed = changed or ["pkg/sales/crypto_payment.py", "pkg/dispatcher.py",
                          "tests/test_crypto_payment.py"]
    repo = make_repo(tmp_path / "repo")
    for path in changed:
        commit(repo, path, "value = 0\n", f"input {path}")
    for path in changed:
        (repo / path).write_text("value = 1\n")
        run_git(repo, "add", path)
    run_git(repo, "commit", "-q", "-m", "fix: several findings")
    head = run_git(repo, "rev-parse", "HEAD").strip()
    r = replace(row(), finding_id=finding, fix_commit=head, run_id=None, summary=summary,
                files=list(row_files if row_files is not None else changed))
    return r, repo


def _refusal(r, repo):
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import _recover_leading_stem

    with pytest.raises(Unavailable) as caught:
        _recover_leading_stem(r, repo, r.repo)
    return str(caught.value)


@pytest.mark.parametrize("summary", [
    "crypto_payment TOCTOU race (FOR UPDATE SKIP LOCKED)",
    "F-07: crypto_payment TOCTOU race",
    "BUG-001: `crypto_payment` race",
])
def test_leading_stem_anchors_the_named_file(tmp_path, summary):
    """Invariant: the summary's leading word, after an id prefix or a backtick, names the one
    changed source file whose stem it is; that file is anchored at file level, no symbol.

    Red 2026-10-29: the pre-change module has no `_recover_leading_stem` (not red proof).
    Mutation in `_leading_word`, the first word replaced by the second; and mutation removing
    `"git-leading-stem-file"` from the accepted methods in `anchor_log._git_provenance` (the
    note is then refused as invalid). Each failed at `assert note is not None`.
    """
    from cca_checks.bugs.anchors import code_sha
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import _recover_leading_stem

    r, repo = _stem_history(tmp_path, summary=summary)
    note = None
    try:
        note = _recover_leading_stem(r, repo, r.repo)
    except Unavailable:
        pass
    assert note is not None, "the file the leading word names was not anchored"
    assert note["anchors"][0]["file"] == "pkg/sales/crypto_payment.py"
    assert note["anchors"] == [{"file": "pkg/sales/crypto_payment.py", "symbol": None,
                                "code_sha": code_sha("value = 1\n")}]
    prov = note["provenance"]
    assert prov["method"] == "git-leading-stem-file"
    assert prov["references"] == [{"file": "pkg/sales/crypto_payment.py",
                                   "token": "crypto_payment"}]
    assert prov["exclusions_version"] == 1
    assert anchor_log.valid(note)


@pytest.mark.parametrize("case,kwargs,reason", [
    ("no_stem", {"summary": "dispatcher_x logger defined after first use"},
     "leading word names no single changed source file"),
    ("generic_word", {"summary": "config default flipped",
                      "changed": ["pkg/config.py", "pkg/other.py"]},
     "leading word is too generic or too short"),
    ("short", {"summary": "db: retry the pool", "changed": ["pkg/db.py", "pkg/other.py"]},
     "leading word is too generic or too short"),
    ("no_word", {"summary": "(no subject) race"}, "summary has no leading identifier"),
    ("non_source", {"summary": "corpus scan race",
                    "changed": ["site/corpus.html", "pkg/other.py", "pkg/third.py"]},
     "leading word names no single changed source file"),
    ("duplicate_stem", {"summary": "crypto_payment race",
                        "changed": ["pkg/a/crypto_payment.py", "pkg/b/crypto_payment.py"]},
     "leading word names no single changed source file"),
    ("missing_from_row", {"row_files": ["pkg/dispatcher.py"]},
     "production file is not in the row's files"),
    ("names_another", {"summary": "crypto_payment race, see dispatcher.py"},
     "summary names another file"),
    ("env_prefix", {"finding": "ENV-3"}, "finding prefix is excluded"),
    ("one_production_file", {"changed": ["pkg/crypto_payment.py", "tests/test_x.py"]},
     "fix changed fewer than two production files"),
])
def test_leading_stem_refusals(tmp_path, case, kwargs, reason):
    """Invariant: each eligibility rule refuses with its own reason.

    Red 2026-10-29, one mutation each, failing at `assert _refusal(...) == reason` (DID NOT
    RAISE, or a different reason): the generic-word check removed (generic_word, short); the
    source-suffix filter removed (non_source); `len(candidates) != 1` became
    `len(candidates) == 0` (duplicate_stem); the row-files check removed (missing_from_row);
    the references check removed (names_another); the prefix check removed (env_prefix);
    the two-file check removed (one_production_file).
    """
    r, repo = _stem_history(tmp_path, **kwargs)
    assert _refusal(r, repo) == reason


def test_a_merge_fix_is_refused(tmp_path):
    """Red 2026-10-29: mutation `len(parents) != 1` became `len(parents) == 0` in
    `_fix_changes`; failed at `assert _refusal(...) == ...` (DID NOT RAISE)."""
    r, repo = _stem_history(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "side", r.fix_commit + "^")
    commit(repo, "pkg/side.py", "value = 3\n", "side")
    run_git(repo, "checkout", "-q", "-")
    run_git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = run_git(repo, "rev-parse", "HEAD").strip()
    assert _refusal(replace(r, fix_commit=merge), repo) == \
        "fix does not have one unambiguous parent"


def test_the_flag_gates_the_rule_and_other_rules_come_first(tmp_path):
    """Invariant: the rule runs only with `leading_stem=True`, only on rows the bullet and
    single-file rules refuse.

    Red 2026-10-29: mutation `if leading_stem:` became `if True:` in `plan`; failed at
    `assert not plain.annotations`. Mutation `if single_file:` became `if False:` in `plan`
    (the single-file rule never tried, so the leading-stem rule meets a one-file fix); failed at
    `assert methods == ["git-single-production-file"]`.
    """
    from cca_checks.bugs.recover_git_anchors import plan

    r, repo = _stem_history(tmp_path)
    ledger = tmp_path / "ledger"
    store.write([r], ledger, "stamp")
    plain = plan(ledger, [repo])
    assert not plain.annotations
    stem = plan(ledger, [repo], leading_stem=True)
    assert [n["provenance"]["method"] for n in stem.annotations] == ["git-leading-stem-file"]

    single, repo2 = _stem_history(tmp_path / "two", changed=["pkg/crypto_payment.py",
                                                              "tests/test_x.py"])
    ledger2 = tmp_path / "ledger2"
    store.write([single], ledger2, "stamp")
    methods = [n["provenance"]["method"] for n in
               plan(ledger2, [repo2], single_file=True, leading_stem=True).annotations]
    assert methods == ["git-single-production-file"]


def test_the_write_is_gated_on_the_ranking_reader(tmp_path, monkeypatch, capsys):
    """Invariant: `--leading-stem --write` writes nothing unless the installed reader ranks
    file-level records as file_only.

    Red 2026-10-29: mutation in `main`, the probe condition `args.single_file or
    args.leading_stem` became `args.single_file`; failed at `assert code == 1`.
    """
    from cca_checks.bugs.recover_git_anchors import main

    r, repo = _stem_history(tmp_path)
    ledger = tmp_path / "ledger"
    store.write([r], ledger, "stamp")
    monkeypatch.setenv("CCA_BUGS_DIR", str(ledger))
    monkeypatch.setenv("CCA_RECALL_ROOT", str(_reader(tmp_path, "old", "VALUE = 1\n")))
    code = main(["--repo", str(repo), "--leading-stem", "--write"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["write_allowed"] is False
    assert not (ledger / anchor_log.NAME).exists()
    monkeypatch.setenv("CCA_RECALL_ROOT", str(_reader(
        tmp_path, "new", "FILE_LEVEL_RANKS_AS_FILE_ONLY = True\n")))
    assert main(["--repo", str(repo), "--leading-stem", "--write"]) == 0
    capsys.readouterr()
    anchored = records.read_ledger(ledger)[0][r.id].anchors
    assert anchored and anchored[0].file == "pkg/sales/crypto_payment.py"


@pytest.mark.parametrize("damage", ["token", "file", "symbol", "parent", "changed", "version"])
def test_the_note_rejects_damage(tmp_path, damage):
    """Invariant: the validator accepts a leading-stem note only with every field intact.

    Red 2026-10-29: mutations in the new validator branch of `anchor_log._git_provenance`,
    deleting the token check, the file comparison, and the parent check, each failed its row at
    `assert not anchor_log.valid(bad)`. (Acceptance of the intact note is proved in
    `test_leading_stem_anchors_the_named_file`.)
    """
    from cca_checks.bugs.recover_git_anchors import _recover_leading_stem

    r, repo = _stem_history(tmp_path)
    note = _recover_leading_stem(r, repo, r.repo)
    assert anchor_log.valid(note)
    bad = json.loads(json.dumps(note))
    prov = bad["provenance"]
    if damage == "token":
        prov["references"][0]["token"] = ""
    elif damage == "file":
        prov["references"][0]["file"] = "pkg/dispatcher.py"
    elif damage == "symbol":
        bad["anchors"][0]["symbol"] = "lease"
    elif damage == "parent":
        prov["parent"] = "abc"
    elif damage == "changed":
        prov["changed_sha256"] = "x"
    else:
        prov["exclusions_version"] = 0
    assert not anchor_log.valid(bad)
