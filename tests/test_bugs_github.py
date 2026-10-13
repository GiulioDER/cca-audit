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


def test_an_unknown_repo_is_found_by_searching_the_commit():
    """Red 2026-10-13: mutation returning Waiting whenever repo is None; failed at ==."""
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


def test_a_malformed_repo_name_from_search_is_unknown():
    """The repo is untrusted too. Red 2026-10-13: mutation removing the repo name check; gh was
    then called with `repos/../etc/passwd`, which the fake runner refuses with AssertionError."""
    run = fake([], search=[{"repository": {"full_name": "../etc/passwd"}}])
    assert isinstance(lookup(None, SHA, run), Unknown)
