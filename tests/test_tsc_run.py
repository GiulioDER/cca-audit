"""`cca_checks.tsc_check`: launching the probe and refusing any answer it cannot vouch for.

No real Node runs here: `subprocess.run` and the tool resolver are replaced on
`cca_checks.tsc_check`, so each test controls exactly what the probe "said".
"""

import json
import subprocess
import sys

import pytest

from cca_checks import config, tsc_check
from cca_checks.toolpath import _is_inside
from cca_checks.tsc_check import (
    COPY_OPTIONS,
    FORCED_OPTIONS,
    SUPPORTED_TS_MAJORS,
    TscResult,
    find_tsconfig,
    run_tsc,
    same_file_key,
    typescript_package,
)


class World:
    """A repo with one source file, a global typescript package, and a fake probe."""

    def __init__(self, tmp_path, monkeypatch):
        self.repo = tmp_path / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "src").mkdir()
        self.file = self.repo / "src" / "app.ts"
        self.file.write_text("export const x = 1;\n")
        self.global_root = tmp_path / "global"
        (self.global_root / "typescript").mkdir(parents=True)
        (self.global_root / "typescript" / "package.json").write_text('{"version": "6.0.3"}')
        self.stdout = json.dumps(self.response())
        self.returncode = 0
        self.raises = None
        self.calls = []
        self.tools = {"node": "node", "npm": "npm"}
        monkeypatch.chdir(self.repo)
        monkeypatch.setattr(tsc_check, "resolve_tool", lambda name, cwd=None: self.tools.get(name))
        monkeypatch.setattr(tsc_check.subprocess, "run", self._run)

    def response(self, **override):
        data = {
            "protocol": 1, "version": "6.0.3", "files": [str(self.file)],
            "diagnostics": [], "any_lines": [3], "assertion_lines": [],
            "directives": {"lines": [], "targets": [], "nocheck": False},
            "subscript_lines": [], "definite_assignment_lines": [],
            "side_effect_import_lines": [], "unresolved_package_side_effect_import": False,
        }
        data.update(override)
        return data

    def set_response(self, **override):
        self.stdout = json.dumps(self.response(**override))

    def _run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        if argv[1:] == ["root", "-g"]:
            return subprocess.CompletedProcess(argv, 0, str(self.global_root) + "\n", "")
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")

    def probe_call(self):
        return next(c for c in self.calls if c[0][0] == "node")


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def test_valid_response_parses(world):
    r = run_tsc(str(world.file), (1, 3))
    assert isinstance(r, TscResult) and 3 in r.any_lines


def test_node_missing_is_none(world):
    world.tools["node"] = None
    assert run_tsc(str(world.file), (1, 3)) is None


def test_typescript_package_missing_is_none(world):
    (world.global_root / "typescript" / "package.json").unlink()
    assert typescript_package() is None
    assert run_tsc(str(world.file), (1, 3)) is None


def test_typescript_package_inside_audited_tree_is_none(world):
    world.global_root = world.repo / "node_modules"
    (world.global_root / "typescript").mkdir(parents=True)
    (world.global_root / "typescript" / "package.json").write_text("{}")
    assert typescript_package() is None


def test_npm_runs_outside_the_audited_tree(world):
    """A project .npmrc in cwd could redirect `npm root -g` to any prefix."""
    typescript_package()
    cwd = next(c for c in world.calls if c[0][0] == "npm")[1]["cwd"]
    assert not _is_inside(cwd, str(world.repo))


def test_package_inside_the_files_repo_but_outside_cwd_is_none(world, tmp_path):
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    claimed = other / "app.ts"
    claimed.write_text("")
    world.global_root = other / "node_modules"
    (world.global_root / "typescript").mkdir(parents=True)
    (world.global_root / "typescript" / "package.json").write_text('{"version": "6.0.3"}')
    world.set_response(files=[str(claimed)])
    assert typescript_package() is not None
    assert run_tsc(str(claimed), (1, 3)) is None


def test_timeout_is_none(world):
    world.raises = subprocess.TimeoutExpired("node", 1)
    assert run_tsc(str(world.file), (1, 3)) is None


def test_nonzero_exit_is_none(world):
    world.returncode = 2
    assert run_tsc(str(world.file), (1, 3)) is None


def test_non_json_stdout_is_none(world):
    world.stdout = "Found 3 errors"
    assert run_tsc(str(world.file), (1, 3)) is None


@pytest.mark.parametrize("field", ["version", "files", "diagnostics", "any_lines",
                                   "assertion_lines", "directives", "subscript_lines",
                                   "definite_assignment_lines", "side_effect_import_lines",
                                   "unresolved_package_side_effect_import"])
def test_missing_field_is_none(world, field):
    data = world.response()
    del data[field]
    world.stdout = json.dumps(data)
    assert run_tsc(str(world.file), (1, 3)) is None


def test_mistyped_field_is_none(world):
    world.set_response(any_lines="3")
    assert run_tsc(str(world.file), (1, 3)) is None


def test_mistyped_side_effect_import_lines_is_none(world):
    """FIX-015: a field the verdict blocks on is validated like the others."""
    world.set_response(side_effect_import_lines=[True])
    assert run_tsc(str(world.file), (1, 3)) is None


@pytest.mark.parametrize("value", [1, "true", None])
def test_mistyped_unresolved_package_side_effect_import_is_none(world, value):
    """FIX-015, L6 REVISE: the flag withholds a confirmation, so only a JSON boolean counts."""
    world.set_response(unresolved_package_side_effect_import=value)
    assert run_tsc(str(world.file), (1, 3)) is None


def test_side_effect_import_lines_are_read(world):
    world.set_response(side_effect_import_lines=[1])
    r = run_tsc(str(world.file), (1, 3))
    assert r is not None
    assert r.side_effect_import_lines == {1}


def test_unsupported_major_is_none(world):
    world.set_response(version=f"{max(SUPPORTED_TS_MAJORS) + 1}.0.0")
    assert run_tsc(str(world.file), (1, 3)) is None


def test_claimed_file_not_in_program_is_none(world):
    world.set_response(files=[str(world.repo / "src" / "other.ts")])
    assert run_tsc(str(world.file), (1, 3)) is None


@pytest.mark.skipif(sys.platform != "win32", reason="case and separators only fold on Windows")
def test_same_file_normalizes_case_and_separators():
    assert same_file_key("C:\\X\\A.ts") == same_file_key("c:/x/a.ts")


def test_run_tsc_accepts_a_relative_path(world):
    # A `..` segment folds under realpath on every OS; the raw strings differ.
    world.set_response(files=[str(world.repo / "src" / ".." / "src" / "app.ts")])
    r = run_tsc("src/app.ts", (1, 3))
    assert r is not None
    sent = json.loads(world.probe_call()[1]["input"])
    assert sent["file"] == str(world.file)
    assert same_file_key(sent["file"]) in r.files


def test_request_carries_the_allow_list_and_forced_options(world):
    run_tsc(str(world.file), (1, 3))
    sent = json.loads(world.probe_call()[1]["input"])
    assert sent["copy_options"] == list(COPY_OPTIONS)
    assert sent["forced_options"] == FORCED_OPTIONS


def test_cwd_is_outside_the_audited_tree(world):
    run_tsc(str(world.file), (1, 3))
    cwd = world.probe_call()[1]["cwd"]
    assert not _is_inside(cwd, str(world.repo))


def test_timeout_uses_ts_timeout(world):
    run_tsc(str(world.file), (1, 3))
    assert world.probe_call()[1]["timeout"] == config.TS_TIMEOUT_S


def test_find_tsconfig_stops_at_git_boundary(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "a.ts").write_text("")
    assert find_tsconfig(str(repo / "a.ts")) is None
    (repo / "tsconfig.json").write_text("{}")
    assert find_tsconfig(str(repo / "a.ts")) == str(repo / "tsconfig.json")


def _manifest(directory, **sections):
    (directory / "package.json").write_text(json.dumps(sections))


def test_declared_types_package_not_installed_is_incomplete(world):
    _manifest(world.repo, devDependencies={"@types/node": "1"})
    r = run_tsc(str(world.file), (1, 3))
    assert r.types_incomplete and r.missing_types == ("@types/node",)


def test_declared_types_package_installed_is_complete(world):
    _manifest(world.repo, devDependencies={"@types/node": "1"}, dependencies={"left": "1"})
    (world.repo / "node_modules" / "@types" / "node").mkdir(parents=True)
    assert run_tsc(str(world.file), (1, 3)).types_incomplete is False


def test_types_package_installed_in_an_ancestor_is_complete(world):
    _manifest(world.repo / "src", peerDependencies={"@types/node": "1"})
    (world.repo / "node_modules" / "@types" / "node").mkdir(parents=True)
    assert run_tsc(str(world.file), (1, 3)).types_incomplete is False


def test_no_package_json_is_complete(world):
    assert run_tsc(str(world.file), (1, 3)).types_incomplete is False


def test_unparseable_package_json_is_incomplete(world):
    (world.repo / "package.json").write_text("{not json")
    assert run_tsc(str(world.file), (1, 3)).types_incomplete is True


@pytest.mark.parametrize("package_json", ['{"version": "7.0.0"}', "{}", "not json"],
                         ids=["major_7", "no_version", "unreadable"])
def test_unsupported_typescript_package_is_none_without_running_the_probe(world, package_json):
    """TypeScript 7 has a package.json but no createProgram: refuse before launching."""
    (world.global_root / "typescript" / "package.json").write_text(package_json)
    assert run_tsc(str(world.file), (1, 3)) is None
    assert not any(c[0][0] == "node" for c in world.calls)


def test_types_check_walks_to_the_filesystem_root_without_git(world, tmp_path):
    """An unpacked tarball has no .git; its package.json sits above src/."""
    tree = tmp_path / "tarball"
    (tree / "src").mkdir(parents=True)
    claimed = tree / "src" / "a.ts"
    claimed.write_text("")
    _manifest(tree, devDependencies={"@types/node": "1"})
    world.set_response(files=[str(claimed)])
    r = run_tsc(str(claimed), (1, 3))
    assert r.types_incomplete and r.missing_types == ("@types/node",)


@pytest.mark.parametrize("section", [[1], "@types/node", None],
                         ids=["list", "string", "null"])
def test_dependency_section_that_is_not_an_object_is_incomplete(world, section):
    """npm would reject such a manifest; its declarations cannot be checked."""
    _manifest(world.repo, devDependencies=section)
    assert run_tsc(str(world.file), (1, 3)).types_incomplete is True
