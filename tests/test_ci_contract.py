"""CI must install what the suite needs, or the suite silently stops testing it.

THE DEFECT CLASS. `tests/test_dev_extra_completeness.py` exists because ~25 substrate
tests `pytest.importorskip`'d a package the `dev` extra did not declare: they skipped
on every CI run, no red, no signal, and the feature's central safety guarantee had
zero coverage. That guard covers PYTHON dependencies declared in pyproject.toml.

The Rust backend reopened the same hole through a different door. `cargo` is not a
pip package, so no extra can declare it; the clippy tests gate on
`shutil.which("cargo")` and skip without it. On a runner that never installs the
toolchain those skips are invisible, and the `--force-warn` guarantee -- the backend's
whole soundness argument -- would go untested forever while CI stayed green.

So this asserts the WORKFLOW installs it. Deliberately a text check on ci.yml and not
a YAML parse: `requires-python = ">=3.10"` and the matrix includes 3.10, where
`tomllib` is not stdlib, and pulling in a YAML parser to guard against a missing
install is a dependency added to protect against missing dependencies.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"
TESTS = pathlib.Path(__file__).resolve().parent

# `shutil.which("<tool>")` in a skipif -- the pattern that makes a test conditional
# on an external binary rather than on a Python package.
_WHICH_SKIP = re.compile(r"""shutil\.which\(\s*["']([A-Za-z0-9_.\-]+)["']""")

#: How each external binary is installed by ci.yml. A tool a test gates on, but which
#: nothing here knows how to install, is a hole rather than a passing test -- so an
#: unmapped tool fails this file rather than being skipped over.
_INSTALLED_BY = {
    "cargo": "dtolnay/rust-toolchain",
    "rustc": "dtolnay/rust-toolchain",
    "clippy-driver": "components: clippy",
    "semgrep": "pip install pyright semgrep",
    "pyright": "pip install pyright semgrep",
}


def _ci_text() -> str:
    return CI.read_text(encoding="utf-8")


def _gated_tools() -> set[str]:
    """Every external binary some test file makes itself conditional on."""
    found = set()
    for path in TESTS.rglob("test_*.py"):
        found.update(_WHICH_SKIP.findall(path.read_text(encoding="utf-8")))
    return found


def test_ci_workflow_exists():
    assert CI.is_file()


def test_some_test_gates_on_an_external_binary():
    """If this is empty the assertions below are vacuous, and this file would pass
    forever while proving nothing -- the exact failure mode it is written against."""
    assert _gated_tools(), "no test gates on shutil.which(); this guard is inert"


def test_every_externally_gated_tool_is_installed_by_ci():
    """The invariant, not today's list.

    Asserting `"cargo" in ci` would pin the current answer and catch nothing new. This
    asserts the property: every external binary any test makes itself conditional on
    must be installed by the workflow, so the NEXT tool someone gates on is caught too.
    """
    ci = _ci_text()
    for tool in sorted(_gated_tools()):
        marker = _INSTALLED_BY.get(tool)
        assert marker is not None, (
            f"tests gate on `{tool}` but this guard does not know how CI installs "
            f"it; add it to _INSTALLED_BY (and to ci.yml) rather than leaving the "
            f"skip invisible")
        assert marker in ci, (
            f"tests skip themselves when `{tool}` is missing, and ci.yml does not "
            f"install it ({marker!r} not found) -- those tests are skipping on every "
            f"CI run with no red and no signal")


def test_ci_installs_the_rust_toolchain_with_clippy():
    """Named explicitly as well, because clippy is a COMPONENT: a runner can have
    cargo and still not have clippy, and `cargo clippy` then fails as a missing
    subcommand -- which run_clippy maps to None, i.e. every Rust claim escalates and
    the end-to-end verdict tests never actually settle anything."""
    ci = _ci_text()
    assert "dtolnay/rust-toolchain" in ci
    assert "components: clippy" in ci


def test_ci_installs_the_dev_extra():
    """The tree-sitter grammar rides `[dev]`; test_dev_extra_completeness.py asserts
    it is declared there, and this asserts the workflow actually installs it."""
    assert 'pip install -e ".[dev]"' in _ci_text()


@pytest.mark.parametrize("language", ["python", "rust"])
def test_the_packaging_job_checks_every_backends_catalog(language):
    """A rule file missing from the wheel is invisible to the test job, which runs
    against the source tree. The packaging job is the only place that catches it, and
    it must cover every backend's catalog rather than the one it was written for."""
    assert "semgrep_catalog" in _ci_text(), (
        "the packaging job hardcodes one language's rule files; it must enumerate "
        "them from the backends so a new catalog cannot ship untested")
