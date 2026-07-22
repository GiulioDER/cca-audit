"""Guard against the exact defect class that caused P1-1.

CI installs `.[dev]`. Any module-scope `pytest.importorskip("<pkg>")` for a
package NOT declared in the `dev` extra of pyproject.toml skips silently on
every CI run -- no red, no signal, and whatever it was guarding (here: ~25
substrate tests, including the integrity gate) gets zero coverage forever.

This does NOT assert today's package list (`assert "mpmath" in dev` would pin
the current answer and catch nothing new -- see feedback-assert-the-invariant-
not-a-drifting-count). It asserts the invariant: every package named in a
module-scope `pytest.importorskip(...)` anywhere under tests/ must appear in
`dev`. That is red at 7b686bd (dev lacked mpmath while two files
importorskip'd it) and green at HEAD, and it also catches the *next* optional
dependency someone forgets to add.

Deliberately does NOT use tomllib/tomli. `requires-python = ">=3.10"` and the
CI matrix includes "3.10", where `tomllib` is not stdlib (3.11+ only). A bare
`import tomllib` would crash on 3.10; guarding it with
`pytest.importorskip("tomli")` would make *this guard itself* skip on 3.10 --
recreating, inside the test meant to prevent it, the exact silent-skip defect
class this file exists to catch. So `dev` is extracted with a dependency-free
regex/text parse of pyproject.toml instead of a TOML parser. Do not
"simplify" this back into `pytest.importorskip("tomli")` or a bare
`import tomllib` -- both reopen the hole.
"""

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TESTS_DIR = pathlib.Path(__file__).resolve().parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# Matches a module-scope (column-0, i.e. not indented inside a function)
# `pytest.importorskip("pkg")` or `alias = pytest.importorskip("pkg")` line.
# Column-0 is what makes it module scope: an indented call only skips the one
# test function it lives in, which already gets normal CI signal for the rest
# of the file.
_IMPORTORSKIP_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*\s*=\s*)?pytest\.importorskip\(\s*[\"']([^\"']+)[\"']",
    re.MULTILINE,
)

# A quoted PEP 508 requirement string, e.g. "mpmath>=1.3" or "pytest>=7".
_REQUIREMENT_RE = re.compile(r"""["']([^"']+)["']""")

# The distribution name at the front of a PEP 508 requirement, before any
# version specifier / extras / environment marker.
_DIST_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")


def _normalize(name: str) -> str:
    """Fold PyPI-style hyphens/case into the same form as import-style names."""
    return name.strip().lower().replace("_", "-")


def _module_scope_importorskip_targets() -> dict[str, list[str]]:
    """Map package name -> sorted list of "relative/path.py:line" that import-skip it at module scope."""
    targets: dict[str, list[str]] = {}
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path == pathlib.Path(__file__).resolve():
            # This file's own docstring/regex text talks about
            # `pytest.importorskip` but never calls it -- nothing to record.
            continue
        text = path.read_text(encoding="utf-8")
        for match in _IMPORTORSKIP_RE.finditer(text):
            pkg = _normalize(match.group(1))
            line_no = text.count("\n", 0, match.start()) + 1
            location = f"{path.relative_to(_REPO_ROOT).as_posix()}:{line_no}"
            targets.setdefault(pkg, []).append(location)
    return targets


def _dev_extra_packages() -> set[str]:
    """Dependency-free regex parse of pyproject.toml's `[project.optional-dependencies]` `dev` list.

    No tomllib/tomli: see module docstring for why a TOML parser is the wrong
    tool here (it would make the guard itself skippable on Python 3.10).
    """
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r"(?m)^dev\s*=\s*\[([^\]]*)\]", text)
    if match is None:
        pytest_fail_reason = (
            "Could not find a top-level `dev = [...]` line under "
            "[project.optional-dependencies] in pyproject.toml. If the extra "
            "was renamed or reformatted across multiple lines with a `]` "
            "inside a string, update the regex in "
            "tests/test_dev_extra_completeness.py::_dev_extra_packages "
            "accordingly -- do not delete this check."
        )
        raise AssertionError(pytest_fail_reason)
    requirements = _REQUIREMENT_RE.findall(match.group(1))
    packages = set()
    for req in requirements:
        dist_match = _DIST_NAME_RE.match(req.strip())
        if dist_match:
            packages.add(_normalize(dist_match.group(0)))
    return packages


def test_every_module_scope_importorskip_target_is_in_dev_extra():
    """Every module-scope `pytest.importorskip("pkg")` under tests/ must have `pkg` in the `dev` extra.

    This is the regression guard for P1-1: the fix (adding mpmath to `dev`)
    shipped with no test, so nothing stops the next person from dropping
    mpmath again, or adding a new module-scope importorskip for a package
    nobody remembered to add to `dev` -- both would go back to silently
    skipping tests under a green CI, undetected, exactly as before.
    """
    required_by_pkg = _module_scope_importorskip_targets()
    declared = _dev_extra_packages()
    missing = {pkg: locs for pkg, locs in required_by_pkg.items() if pkg not in declared}

    assert not missing, (
        "The following packages are import-skipped at MODULE SCOPE somewhere "
        "under tests/, but are NOT declared in the `dev` extra of "
        "pyproject.toml. CI installs `.[dev]`, so every test in the listed "
        "file(s) silently SKIPS instead of running -- zero CI signal, no red, "
        "no warning. Add each package below to `dev = [...]` in "
        "pyproject.toml to fix:\n"
        + "\n".join(
            f"  - {pkg!r} (needed by: {', '.join(locs)})"
            for pkg, locs in sorted(missing.items())
        )
    )
