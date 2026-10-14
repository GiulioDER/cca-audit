"""lookup() against a fake gh, and gh() against a fake subprocess."""
import json
import subprocess
from datetime import datetime, timezone

import pytest

import cca_checks.bugs.github as github
from cca_checks.bugs.forward import Merge
from cca_checks.bugs.github import Abandoned, GhError, Unknown, Waiting, gh, lookup

SHA = "a" * 40
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _acme_is_allowed(monkeypatch):
    """Tests that exercise the gh login default unset or blank this themselves."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "acme")


def _api(name, number=7):
    return {"repository_url": f"https://api.github.com/repos/{name}", "number": number}


def fake(pulls, *, default="master", search=None, by_owner=None, queries=None):
    """`search` answers every PR search; `by_owner` answers only the named owner's."""
    def run(args):
        path = args[-1] if args[0] == "api" else ""
        if "search/issues" in args:
            q = args[-1]
            if queries is not None:
                queries.append(q)
            names = list(search or [])
            for owner, more in (by_owner or {}).items():
                if q.endswith(f" is:pr user:{owner}"):
                    names += more
            return json.dumps({"items": [_api(n) for n in names]})
        if "search/commits" in args:  # GitHub finds no squashed commit here
            return json.dumps({"items": []})
        if path == "repos/acme/widgets":
            return json.dumps({"default_branch": default})
        if path == f"repos/acme/widgets/commits/{SHA}/pulls":
            return json.dumps(pulls)
        raise AssertionError(args)
    return run


def pr(number, *, merged=None, state="closed", base="master", merge_sha="b" * 40,
       closed="2026-10-01T00:00:00Z"):
    return {"number": number, "state": state, "merged_at": merged,
            "closed_at": closed if state == "closed" else None,
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


def test_an_unknown_repo_is_found_through_a_pull_request_search(monkeypatch):
    """C1. Invariant: a record without a repo is matched by searching pull requests for the
    sha, restricted to an allowed owner; GitHub's commit search sees only default
    branches, which a squash merge never puts the run's commit on.

    Red 2026-10-13: mutation reverting `_search_repo` to `search/commits q=hash:<sha>`
    (the fake answers that search with no hits, as GitHub does for a squashed commit);
    failed at the == (got Waiting)."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "acme")
    queries = []
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")], by_owner={"acme": ["acme/widgets"]},
               queries=queries)
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)
    assert queries == [f"q={SHA} is:pr user:acme"]


def test_every_allowed_owner_is_searched(monkeypatch):
    """C1. Invariant: one PR search per allowed owner, until one finds the commit.

    Red 2026-10-13: mutation searching only `owners[0]`; failed at the == (got Waiting)."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "first,acme")
    queries = []
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")], by_owner={"acme": ["acme/widgets"]},
               queries=queries)
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)
    assert queries == [f"q={SHA} is:pr user:first", f"q={SHA} is:pr user:acme"]


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
    run = fake([], search=["acme/wid gets"])
    assert isinstance(lookup(None, SHA, run), Unknown)


def test_a_foreign_first_hit_is_skipped_for_an_owned_one(monkeypatch):
    """A fork carrying the same sha must not answer. Red 2026-10-13: mutation taking items[0]
    without the owner filter; the fake runner
    refused `repos/attacker/RE-call` with AssertionError."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", " ACME ,other")
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")],
               search=["attacker/RE-call", "acme/widgets"])
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)


def test_only_foreign_hits_are_waiting(monkeypatch):
    """Red 2026-10-13: mutation accepting any owner; the fake runner refused
    `repos/acme2/widgets` with AssertionError."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "acme")
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")], search=["acme2/widgets"])
    got = lookup(None, SHA, run)
    assert isinstance(got, Waiting) and "acme" in got.reason


def test_owners_default_to_the_gh_login(monkeypatch):
    """Red 2026-10-13: mutation ignoring `gh api user` and returning no owners; failed at the ==
    (got Waiting)."""
    monkeypatch.delenv("CCA_BUGS_OWNERS", raising=False)
    inner = fake([pr(7, merged="2026-10-14T09:00:00Z")],
                 search=["attacker/x", "acme/widgets"])

    def run(args):
        return json.dumps({"login": "Acme"}) if args == ["api", "user"] else inner(args)
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)


def test_a_failing_login_lookup_is_unknown(monkeypatch):
    """Red 2026-10-13: mutation swallowing the login error as an empty owner list; failed at
    isinstance(got, Unknown)."""
    monkeypatch.setenv("CCA_BUGS_OWNERS", "")
    inner = fake([], search=["acme/widgets"])

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


def _pulls_fail(message):
    inner = fake([])

    def run(args):
        if args[-1].endswith("/pulls"):
            raise GhError(f"gh api repos/acme/widgets/commits/{SHA}/pulls exited 1: {message}")
        return inner(args)
    return run


@pytest.mark.parametrize("message", [
    f"gh: No commit found for SHA: {SHA} (HTTP 422)", "gh: Not Found (HTTP 404)"])
def test_a_commit_github_never_received_is_waiting(message):
    """I1. Invariant: a sha GitHub does not have (rebased or amended before the first push)
    waits, so the stale report reaches it, instead of being unknown forever.

    Red 2026-10-13: mutation deleting the `_commit_missing` branch (every failure of the
    pulls call re-raised); failed at `assert got == Waiting(...)` (got Unknown)."""
    got = lookup("acme/widgets", SHA, _pulls_fail(message))
    assert got == Waiting("GitHub has not received the commit")


def test_any_other_failure_of_the_pulls_call_stays_unknown():
    """I1. Invariant: only a missing commit waits; an outage is still unknown (exit 3).

    Red 2026-10-13: mutation returning Waiting for every GhError on the pulls call; failed
    at isinstance(got, Unknown)."""
    got = lookup("acme/widgets", SHA, _pulls_fail("gh: Server Error (HTTP 502)"))
    assert isinstance(got, Unknown)


def test_a_pr_merged_before_the_run_closed_is_ignored():
    """I2. Invariant: a run closed at its base commit is not attributed to the earlier PR
    that merged that commit: PRs merged before `not_before` do not count.

    Red 2026-10-13: mutation deleting the `not_before` filter; failed at
    `assert isinstance(early, Waiting) ...` (got the earlier PR's Merge)."""
    run = fake([pr(7, merged="2026-10-10T09:00:00Z")])
    closed = datetime(2026, 10, 12, tzinfo=timezone.utc)
    early = lookup("acme/widgets", SHA, run, not_before=closed)
    assert isinstance(early, Waiting) and "before the run closed" in early.reason
    later = fake([pr(7, merged="2026-10-10T09:00:00Z"),
                  pr(8, merged="2026-10-13T09:00:00Z", merge_sha="c" * 40)])
    assert lookup("acme/widgets", SHA, later, not_before=closed) == Merge(
        "acme/widgets", "c" * 40, "2026-10-13", 8)


def test_abandoned_only_after_a_day_closed():
    """I4. Invariant: a record is abandoned (terminal) only when every PR listing the
    commit has been closed unmerged for more than 24 hours; a PR closed an hour ago may
    be reopened, so it waits.

    Red 2026-10-13: mutation dropping the 24 hour check (`settled = True`); failed at
    `assert isinstance(recent, Waiting)` (got Abandoned)."""
    recent = lookup("acme/widgets", SHA, fake([pr(7, closed="2026-10-14T11:00:00Z")]),
                    now=NOW)
    assert isinstance(recent, Waiting)
    mixed = fake([pr(7), pr(8, closed="2026-10-14T11:00:00Z")])
    assert isinstance(lookup("acme/widgets", SHA, mixed, now=NOW), Waiting)
    old = lookup("acme/widgets", SHA, fake([pr(7, closed="2026-10-13T11:00:00Z")]), now=NOW)
    assert isinstance(old, Abandoned)


def _outcome(args):
    try:
        return gh(args)
    except Exception as exc:  # the test asserts which kind escaped
        return exc


def test_the_real_gh_runner_maps_every_failure_to_gh_error(monkeypatch):
    """Invariant: gh() turns a missing gh, a timeout and a non-zero exit into GhError (so
    lookup answers Unknown, or Waiting for a missing commit), and returns stdout on 0.

    Red 2026-10-13: mutation narrowing the except to `subprocess.TimeoutExpired`; failed at
    `assert isinstance(missing, GhError) ...` (got FileNotFoundError).
    Red 2026-10-13: mutation deleting the returncode check; failed at
    `assert isinstance(failed, GhError) ...` (got the stdout string)."""
    def missing_gh(*a, **k):
        raise FileNotFoundError(2, "No such file", "gh")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(["gh"], 60)

    def exit_1(*a, **k):
        return subprocess.CompletedProcess(
            a[0], 1, stdout="{}",
            stderr=f"gh: No commit found for SHA: {SHA} (HTTP 422)\nsecond line")

    def ok(*a, **k):
        return subprocess.CompletedProcess(a[0], 0, stdout="[]", stderr="")

    monkeypatch.setattr(github.subprocess, "run", missing_gh)
    missing = _outcome(["api", "user"])
    assert isinstance(missing, GhError) and "could not run" in str(missing)
    monkeypatch.setattr(github.subprocess, "run", slow)
    timed_out = _outcome(["api", "user"])
    assert isinstance(timed_out, GhError) and "could not run" in str(timed_out)
    monkeypatch.setattr(github.subprocess, "run", exit_1)
    failed = _outcome(["api", f"repos/acme/widgets/commits/{SHA}/pulls"])
    assert isinstance(failed, GhError) and "exited 1" in str(failed)
    assert "second line" not in str(failed) and github._commit_missing(failed)
    monkeypatch.setattr(github.subprocess, "run", ok)
    assert _outcome(["api", "user"]) == "[]"


MISSING = "missing"  # the repository's commits/<sha>/pulls answers 422


def multi(pulls_by_repo, hits):
    """A fake gh over several repositories; `hits` are the PR search's (repo, number)."""
    def run(args):
        path = args[-1]
        if "search/issues" in args:
            return json.dumps({"items": [_api(n, k) for n, k in hits]})
        for name, pulls in pulls_by_repo.items():
            if path == f"repos/{name}":
                return json.dumps({"default_branch": "master"})
            if path == f"repos/{name}/commits/{SHA}/pulls":
                if pulls == MISSING:
                    raise GhError(f"gh api {path} exited 1: gh: No commit found for SHA: "
                                  f"{SHA} (HTTP 422)")
                return json.dumps(pulls)
        raise AssertionError(args)
    return run


def test_a_mention_only_hit_does_not_hide_the_real_repository():
    """Invariant: a PR in another repository of the same owner that only mentions the sha
    (its pulls call answers 422) is skipped, and the next candidate decides.

    Red 2026-10-13: mutation judging only the first candidate (`candidates[:1]`); failed
    at the == (got Waiting)."""
    run = multi({"acme/other": MISSING,
                 "acme/widgets": [pr(7, merged="2026-10-14T09:00:00Z")]},
                [("acme/other", 3), ("acme/widgets", 7)])
    assert lookup(None, SHA, run) == Merge("acme/widgets", "b" * 40, "2026-10-14", 7)


def test_a_closed_pr_in_another_repository_cannot_abandon_the_record():
    """Invariant: abandoned only when every repository that holds the commit abandoned
    it; a PR a stranger opened into acme/other and closed a day ago, while the real PR
    in acme/widgets is open, leaves the record waiting.

    Red 2026-10-13: mutation returning the first non-skipped candidate's answer
    (`if got is not _SKIP: return got`); failed at `assert isinstance(got, Waiting)`
    (got Abandoned)."""
    run = multi({"acme/other": [pr(5, closed="2026-10-12T00:00:00Z")],
                 "acme/widgets": [pr(7, state="open")]},
                [("acme/other", 5), ("acme/widgets", 7)])
    got = lookup(None, SHA, run, now=NOW)
    assert isinstance(got, Waiting)
    both = multi({"acme/other": [pr(5, closed="2026-10-12T00:00:00Z")],
                  "acme/widgets": [pr(7, closed="2026-10-12T00:00:00Z")]},
                 [("acme/other", 5), ("acme/widgets", 7)])
    assert isinstance(lookup(None, SHA, both, now=NOW), Abandoned)


def test_only_mention_only_hits_are_waiting_with_the_right_reason():
    """Invariant: when no searched repository holds the commit (its pulls call answers
    422, or lists none of the searched PRs), the record waits and says so, rather than
    claiming GitHub never received the commit.

    Red 2026-10-13: mutation deleting the hit number check in `_judge`; failed at
    `assert got == Waiting(...)` (got Abandoned from acme/widgets' unrelated #99)."""
    run = multi({"acme/other": MISSING,
                 "acme/widgets": [pr(99, closed="2026-10-01T00:00:00Z")]},
                [("acme/other", 3), ("acme/widgets", 7)])
    got = lookup(None, SHA, run, now=NOW)
    assert got == Waiting("no searched repository holds the commit")


def test_an_explicit_repo_with_a_foreign_owner_is_unknown():
    """Invariant: a repository named in meta.json (untrusted when harvested) passes the
    same owner allowlist as a searched one.

    Red 2026-10-13: mutation deleting the owner check on the explicit repo path; failed
    at `assert isinstance(got, Unknown) ...` (got a Merge in attacker/widgets)."""
    run = multi({"attacker/widgets": [pr(7, merged="2026-10-14T09:00:00Z")]}, [])
    got = lookup("attacker/widgets", SHA, run)
    assert isinstance(got, Unknown) and "allowed owner" in got.reason
