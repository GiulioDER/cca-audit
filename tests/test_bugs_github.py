"""lookup() against a fake gh."""
import json

import pytest

from cca_checks.bugs.forward import Merge
from cca_checks.bugs.github import Abandoned, GhError, Unknown, Waiting, lookup

SHA = "a" * 40


def fake(pulls, *, default="master", search=None):
    def run(args):
        path = args[-1] if args[0] == "api" else ""
        if "search/commits" in args:
            return json.dumps({"items": search or []})
        if path == "repos/acme/widgets":
            return json.dumps({"default_branch": default})
        if path == f"repos/acme/widgets/commits/{SHA}/pulls":
            return json.dumps(pulls)
        raise AssertionError(args)
    return run


def pr(number, *, merged=None, state="closed", base="master", merge_sha="b" * 40):
    return {"number": number, "state": state, "merged_at": merged,
            "merge_commit_sha": merge_sha, "base": {"ref": base}}


def test_a_squash_merged_pr_gives_its_merge_commit():
    """Red 2026-10-13: mutation returning `commit` instead of `merge_commit_sha`; failed at the
    equality below."""
    got = lookup("acme/widgets", SHA, fake([pr(7, merged="2026-10-14T09:00:00Z")]))
    assert got == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)


def test_a_pr_merged_into_another_base_is_still_waiting():
    """Red 2026-10-13: mutation dropping the base == default check; failed at isinstance."""
    got = lookup("acme/widgets", SHA, fake([pr(7, merged="2026-10-14T09:00:00Z", base="stack")]))
    assert isinstance(got, Waiting)


def test_closed_without_merging_is_abandoned_but_open_is_waiting():
    """Red 2026-10-13: mutation treating any closed-unmerged PR in the list as abandoned
    (`any` for `all`); failed at the second isinstance."""
    assert isinstance(lookup("acme/widgets", SHA, fake([pr(7)])), Abandoned)
    assert isinstance(lookup("acme/widgets", SHA, fake([pr(7), pr(8, state="open")])), Waiting)


def test_no_pr_lists_the_commit_is_waiting_not_abandoned():
    """Review Focus 2. Red 2026-10-13: mutation returning Abandoned for an empty list."""
    assert isinstance(lookup("acme/widgets", SHA, fake([])), Waiting)


def test_an_unknown_repo_is_found_by_searching_the_commit(monkeypatch):
    """Red 2026-10-13: mutation returning Waiting whenever repo is None; failed at ==."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "acme")
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")],
               search=[{"repository": {"full_name": "acme/widgets"}}])
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)


@pytest.mark.parametrize("err", [GhError("gh: not logged in"), ValueError("bad json")])
def test_any_gh_failure_is_unknown(err):
    """Review Focus 3. Red 2026-10-13: mutation narrowing the except to GhError only; the
    ValueError case raises instead of returning Unknown."""
    def run(args):
        raise err
    assert isinstance(lookup("acme/widgets", SHA, run), Unknown)


@pytest.mark.parametrize("bad", ["abc123", "B" * 40, "g" * 40, "b" * 40 + "\n", None, "../" + "b" * 37])
def test_a_malformed_merge_sha_is_unknown_not_a_merge(bad):
    """The sha becomes a ledger key and a memo filename. Red 2026-10-13: mutation removing the
    40-hex check on merge_commit_sha; failed at isinstance(got, Unknown)."""
    got = lookup("acme/widgets", SHA,
                 fake([pr(7, merged="2026-10-14T09:00:00Z", merge_sha=bad)]))
    assert isinstance(got, Unknown)


def test_a_malformed_repo_name_from_search_is_unknown(monkeypatch):
    """The repo is untrusted too. Red 2026-10-13: mutation removing the repo name check; gh was
    then called with `repos/acme/wid gets`, which the fake runner refuses with AssertionError."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "acme")
    run = fake([], search=[{"repository": {"full_name": "acme/wid gets"}}])
    assert isinstance(lookup(None, SHA, run), Unknown)


def _hit(name):
    return {"repository": {"full_name": name}}


def test_a_foreign_first_hit_is_skipped_for_an_owned_one(monkeypatch):
    """A fork carrying the same sha must not answer. Red 2026-10-13: mutation taking items[0]
    without the owner filter; the fake runner
    refused `repos/attacker/RE-call` with AssertionError."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", " ACME ,other")
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")],
               search=[_hit("attacker/RE-call"), _hit("acme/widgets")])
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)


def test_only_foreign_hits_are_waiting(monkeypatch):
    """Red 2026-10-13: mutation accepting any owner; the fake runner refused
    `repos/acme2/widgets` with AssertionError."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "acme")
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")], search=[_hit("acme2/widgets")])
    got = lookup(None, SHA, run)
    assert isinstance(got, Waiting) and "acme" in got.reason


def test_owners_default_to_the_gh_login(monkeypatch):
    """Red 2026-10-13: mutation ignoring `gh api user` and returning no owners; failed at the ==
    (got Waiting)."""
    monkeypatch.delenv("CCA_BUGS_OWNERS", raising=False)
    inner = fake([pr(7, merged="2026-10-14T09:00:00Z")],
                 search=[_hit("attacker/x"), _hit("acme/widgets")])

    def run(args):
        return json.dumps({"login": "Acme"}) if args == ["api", "user"] else inner(args)
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)


def test_a_failing_login_lookup_is_unknown(monkeypatch):
    """Red 2026-10-13: mutation swallowing the login error as an empty owner list; failed at
    isinstance(got, Unknown)."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "")
    inner = fake([], search=[_hit("acme/widgets")])

    def run(args):
        if args == ["api", "user"]:
            raise GhError("gh: not logged in")
        return inner(args)
    assert isinstance(lookup(None, SHA, run), Unknown)


@pytest.mark.parametrize("name", ["../..", "./.", "acme/..", "../widgets"])
def test_a_dots_only_segment_is_unknown(name):
    """Red 2026-10-13: mutation reverting _REPO to the plain character class; failed at
    pytest.fail in the runner (gh called with `repos/../..`)."""
    got = lookup(name, SHA, lambda args: pytest.fail(f"gh called: {args}"))
    assert isinstance(got, Unknown)


@pytest.mark.parametrize("commit", ["abc", "A" * 40, "a" * 41, "../" + "a" * 37, ""])
def test_a_non_hex_commit_is_unknown_and_asks_gh_nothing(commit):
    """Red 2026-10-13: mutation removing the commit check; failed at pytest.fail inside the
    runner (gh was called)."""
    got = lookup("acme/widgets", commit, lambda args: pytest.fail(f"gh called: {args}"))
    assert isinstance(got, Unknown)
