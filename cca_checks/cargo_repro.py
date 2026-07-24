"""Confirm a Rust claim by running a generated test that actually panics.

WHY THIS EXISTS AT ALL. `clippy_check` deliberately cannot CONFIRM a `panic_path` or
an `unsafe_op`: a lint sees the construct, not whether it is reachable with a value
the caller controls, and `.unwrap()` on a value built two lines above is correct code.
Without an execution path those claim types could only ever refute, which would make
half the Rust vocabulary unable to find anything. This is that path -- the analogue of
`repro_runner` for Python, and it carries the same warning.

WARNING: this executes the target's code. `cargo test` compiles and runs the crate's
build script and test harness, so pointing it at a repo you do not trust runs that
repo's code with your privileges and environment. Do not use it on untrusted code
without a sandbox (container / seccomp / a scrubbed, offline env).

IT WRITES INTO THE AUDITED CRATE, AND THAT IS UNAVOIDABLE. A Rust integration test has
to live inside the crate for `cargo test --test <name>` to address it, so unlike
`repro_runner` -- whose generated file can sit anywhere -- the caller must place the
test in `<crate>/tests/` and delete it afterwards. Builds land in the crate's own
`target/`; see `run_repro` for why that is the right default here and not in
`clippy_check`.

THE BUILD IS CHECKED SEPARATELY, AND THAT IS THE WHOLE SOUNDNESS ARGUMENT. `cargo
test` exits non-zero for a failing test AND for a crate that does not compile, and
"reproduced" versus "your fixture does not build" are opposite answers. Worse, since
confirmation only requires the predicted message to appear in the output, a claim
predicting a word that happens to occur in a compiler error would be CONFIRMED by the
build failing. So `--no-run` runs first: it compiles without executing, and anything
short of a clean build escalates before a single line of the target runs. An
environment gap must never manufacture evidence.
"""

import os
import subprocess

from .claim import Verdict, make_verdict
from .config import RUST_TIMEOUT_S
from .toolpath import resolve_tool

SOURCE = "cargo"


def _uncertain(finding_id: str, why: str) -> Verdict:
    return make_verdict(finding_id, "UNCERTAIN", f"{why}; escalated", SOURCE)


def _manifest_for(path: str) -> str | None:
    """Nearest `Cargo.toml` at or above `path`'s directory, or None.

    Shared reasoning with `clippy_check._manifest_for`: guessing a workspace root
    would run a different crate's tests than the claim is about.
    """
    current = os.path.dirname(os.path.abspath(path))
    while True:
        candidate = os.path.join(current, "Cargo.toml")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None


def run_repro(finding_id: str, test_path: str, expected_error: str | None,
              target_dir: str | None = None) -> Verdict:
    """Run the integration test at `test_path` and confirm on `expected_error`.

    `test_path` is a `.rs` file the caller has already written into the crate's
    `tests/` directory -- the same contract `repro_runner.run_repro` has for a
    generated pytest file. The test NAME is the file's stem, which is how
    `cargo test --test <name>` addresses it.

    CONFIRMED requires all three: the crate builds, the test fails, and the predicted
    message appears. Anything else escalates.
    """
    exe = resolve_tool("cargo")
    if exe is None:
        # Missing from PATH, or resolved inside the audited tree (hijack attempt).
        return _uncertain(finding_id, "repro could not run (cargo unavailable)")

    manifest = _manifest_for(test_path)
    if manifest is None:
        return _uncertain(finding_id,
                          f"repro could not run (no Cargo.toml at or above "
                          f"{os.path.basename(test_path)})")

    name = os.path.splitext(os.path.basename(test_path))[0]
    base = [exe, "test", "--manifest-path", manifest, "--test", name]
    # `target_dir` defaults to the CRATE'S OWN, unlike clippy_check, which always
    # isolates. The two differ because the reasons differ. clippy_check isolates
    # because a warm cache can report a build with nothing to say, which is
    # indistinguishable from a clean crate -- silence is its evidence. A repro's
    # evidence is a panic message, which a warm cache cannot fabricate, so freshness
    # buys nothing here and a cold rebuild of every dependency would cost minutes per
    # confirmation. The caller has in any case already written a test file into the
    # crate's `tests/`, which a Rust integration test requires; `target/` is
    # gitignored in essentially every Rust repo. Pass `target_dir` to isolate anyway.
    if target_dir:
        base += ["--target-dir", target_dir]

    built = _run([*base, "--no-run"], RUST_TIMEOUT_S)
    if built is None:
        return _uncertain(finding_id,
                          f"repro build timed out after {RUST_TIMEOUT_S}s or could "
                          f"not be launched")
    if built.returncode != 0:
        # The crate does not compile. NOT a reproduction: a compiler error is not
        # evidence about runtime behaviour, and confirming on it would let any
        # predicted string that appears in a build log settle a claim.
        tail = ((built.stdout or "") + (built.stderr or ""))[-800:]
        return _uncertain(finding_id,
                          f"repro could not be built (cargo rc={built.returncode}), so "
                          f"nothing was executed:\n{tail}")

    proc = _run(base, RUST_TIMEOUT_S)
    if proc is None:
        return _uncertain(finding_id, f"repro timed out after {RUST_TIMEOUT_S}s")

    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-800:]

    if proc.returncode == 0:
        return _uncertain(finding_id,
                          "repro did not trigger the impact through the validated "
                          "boundary")
    if not expected_error:
        return _uncertain(finding_id,
                          f"repro failed but no predicted error to confirm "
                          f"against:\n{tail}")
    if expected_error not in out:
        return _uncertain(finding_id,
                          f"repro failed but not with '{expected_error}':\n{tail}")
    return make_verdict(finding_id, "CONFIRMED",
                        f"repro reproduced the impact:\n{tail}", SOURCE)
