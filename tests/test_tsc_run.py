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
        (self.global_root / "typescript" / "package.json").write_text("{}")
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
            "subscript_lines": [],
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
                                   "assertion_lines", "directives", "subscript_lines"])
def test_missing_field_is_none(world, field):
    data = world.response()
    del data[field]
    world.stdout = json.dumps(data)
    assert run_tsc(str(world.file), (1, 3)) is None


def test_mistyped_field_is_none(world):
    world.set_response(any_lines="3")
    assert run_tsc(str(world.file), (1, 3)) is None


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
    world.set_response(files=[str(world.file)])
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
