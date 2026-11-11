"""TypeScript claims settled end to end through the CLI, by the real compiler.

Needs `node` and a global TypeScript install; skipped otherwise. Fixture lines are the
defect lines recorded in tests/test_ts_probe.py.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

from cca_checks.toolpath import resolve_tool

pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and shutil.which("tsc")),
    reason="node and tsc are required for the TypeScript acceptance suite")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = "tests/fixtures/typescript"


def check(claim_type, fixture, line, cwd=ROOT, file=None):
    """Run the CLI on one claim and return the parsed verdict."""
    argv = [sys.executable, "-m", "cca_checks", "check", "--claim-type", claim_type,
            "--finding-id", "X", "--file", file or f"{FIXTURES}/{fixture}",
            "--line", str(line)]
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")}
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=env, timeout=600)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def test_undefined_name_confirms():
    assert check("definedness", "basic/undefined_name.ts", 2)["verdict"] == "CONFIRMED"


def test_defined_name_refutes():
    assert check("definedness", "basic/defined_name.ts", 3)["verdict"] == "FALSE_POSITIVE"


def test_null_access_confirms():
    assert check("nullability", "basic/null_access.ts", 3)["verdict"] == "CONFIRMED"


def test_null_guarded_refutes():
    assert check("nullability", "basic/null_guarded.ts", 4)["verdict"] == "FALSE_POSITIVE"


def test_bad_arg_confirms():
    assert check("type", "basic/bad_arg.ts", 5)["verdict"] == "CONFIRMED"


def test_good_arg_refutes():
    assert check("type", "basic/good_arg.ts", 5)["verdict"] == "FALSE_POSITIVE"


@pytest.mark.parametrize("fixture, claim_type, line", [
    ("basic/suppressed_ignore.ts", "nullability", 4),
    ("basic/suppressed_expect.ts", "nullability", 4),
    ("basic/suppressed_nocheck.ts", "nullability", 4),
    ("basic/explicit_any.ts", "nullability", 2),
    ("basic/as_any.ts", "nullability", 3),
    ("basic/json_parse.ts", "nullability", 2),
    ("basic/any_helper.ts", "nullability", 3),
    ("basic/ambient_import.ts", "nullability", 3),
    ("basic/implicit_any.ts", "nullability", 2),
    ("basic/missing_module.ts", "nullability", 3),
    ("basic/index_access.ts", "nullability", 3),
    ("basic/multiline.ts", "nullability", 7),
    ("types_node/uses_process.ts", "nullability", 2),
])
def test_guard_never_refutes(fixture, claim_type, line):
    assert check(claim_type, fixture, line)["verdict"] != "FALSE_POSITIVE"


def test_directive_in_string_still_refutes():
    assert check("nullability", "basic/directive_in_string.ts", 5)["verdict"] == "FALSE_POSITIVE"


@pytest.mark.parametrize("fixture", ["weak/null_access.ts", "nocheck_config/null_access.ts"])
def test_weak_configs_still_confirm(fixture):
    assert check("nullability", fixture, 3)["verdict"] == "CONFIRMED"


def test_solution_config_confirms():
    assert check("nullability", "solution/src/main.ts", 4)["verdict"] == "CONFIRMED"


def test_process_env_does_not_confirm_definedness():
    assert check("definedness", "types_node/uses_process.ts", 2)["verdict"] != "CONFIRMED"


def test_crlf_file_confirms_at_the_same_line(tmp_path):
    src = os.path.join(ROOT, FIXTURES, "basic")
    with open(os.path.join(src, "null_access.ts"), "rb") as f:
        lf = f.read().replace(b"\r\n", b"\n")
    (tmp_path / "null_access.ts").write_bytes(lf.replace(b"\n", b"\r\n"))
    shutil.copy(os.path.join(src, "tsconfig.json"), tmp_path / "tsconfig.json")
    v = check("nullability", None, 3, cwd=str(tmp_path), file="null_access.ts")
    assert v["verdict"] == "CONFIRMED"


def test_tsx_component_confirms():
    assert check("nullability", "tsx/component.tsx", 3)["verdict"] == "CONFIRMED"


def test_node_in_audited_root_is_refused(tmp_path, monkeypatch):
    """The planted binary must never run; no verdict is asserted, only the resolver."""
    for name in ("node", "node.exe"):
        (tmp_path / name).write_text("not a real node")
    monkeypatch.chdir(tmp_path)
    found = resolve_tool("node")
    assert found is None or os.path.dirname(os.path.realpath(found)) != str(tmp_path.resolve())
