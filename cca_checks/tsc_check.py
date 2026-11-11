import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from .config import TS_TIMEOUT_S
from .toolpath import PROBE_TIMEOUT_S, _is_inside, resolve_tool

PROTOCOL = 1
PROBE = os.path.join(os.path.dirname(__file__), "ts_probe.js")

# One supported major, pinned in CI; any other major is UNCERTAIN.
SUPPORTED_TS_MAJORS = frozenset({6})

COPY_OPTIONS = (
    "baseUrl", "paths", "rootDirs", "moduleResolution", "module", "moduleSuffixes",
    "customConditions", "jsx", "jsxImportSource", "lib", "target", "types", "typeRoots",
    "allowArbitraryExtensions", "resolveJsonModule", "esModuleInterop",
    "allowSyntheticDefaultImports", "experimentalDecorators", "useDefineForClassFields",
)
FORCED_OPTIONS = {
    "strict": True,
    # Every option TypeScript 6.0.3 marks `strictFlag`, so no inherited false survives.
    "noImplicitAny": True,
    "strictNullChecks": True,
    "strictFunctionTypes": True,
    "strictBindCallApply": True,
    "strictPropertyInitialization": True,
    "strictBuiltinIteratorReturn": True,
    "noImplicitThis": True,
    "useUnknownInCatchVariables": True,
    "noCheck": False,
    "noEmit": True,
    "skipLibCheck": True,
    "allowJs": False,
    "checkJs": False,
    "incremental": False,
    "composite": False,
    # TypeScript 6 reports baseUrl, node10 and es5 as errors; this silences only those.
    "ignoreDeprecations": "6.0",
}


@dataclass(frozen=True)
class TscDiagnostic:
    code: int
    message: str
    file: str | None
    start_line: int | None
    end_line: int | None


@dataclass(frozen=True)
class TscResult:
    version: str
    files: frozenset[str]
    diagnostics: tuple[TscDiagnostic, ...]
    any_lines: frozenset[int]
    assertion_lines: frozenset[int]
    directive_lines: frozenset[int]
    directive_targets: frozenset[int]
    nocheck: bool
    subscript_lines: frozenset[int]


def same_file_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _scan_up(path: str) -> tuple[str | None, str | None]:
    """(nearest tsconfig.json, .git root) walking up from the file's directory.

    The walk stops after the first directory holding `.git`, so a config above the
    repository is never picked up. The root is None when no `.git` was reached.
    """
    directory = os.path.dirname(os.path.abspath(path))
    config = None
    while True:
        candidate = os.path.join(directory, "tsconfig.json")
        if config is None and os.path.isfile(candidate):
            config = candidate
        if os.path.exists(os.path.join(directory, ".git")):
            return config, directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return config, None
        directory = parent


def find_tsconfig(path: str) -> str | None:
    return _scan_up(path)[0]


def typescript_package() -> str | None:
    """The globally installed `typescript` directory, or None if absent or untrusted."""
    npm = resolve_tool("npm")
    if npm is None:
        return None
    try:
        done = subprocess.run([npm, "root", "-g"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=PROBE_TIMEOUT_S,
                              cwd=tempfile.gettempdir())
    except (subprocess.TimeoutExpired, OSError):
        return None
    root = done.stdout.strip()
    if done.returncode != 0 or not root:
        return None
    package = os.path.join(root, "typescript")
    if not os.path.isfile(os.path.join(package, "package.json")):
        return None
    if _is_inside(os.path.realpath(package), os.path.realpath(os.getcwd())):
        return None
    return package


def _is_int(value: object) -> bool:
    # bool is an int subclass; a JSON `true` is malformed, not 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _lines(value: object) -> frozenset[int] | None:
    if not isinstance(value, list) or not all(_is_int(v) for v in value):
        return None
    return frozenset(value)


def _diagnostic(raw: object) -> TscDiagnostic | None:
    if not isinstance(raw, dict):
        return None
    code, message, file = raw.get("code"), raw.get("message"), raw.get("file")
    start, end = raw.get("start_line"), raw.get("end_line")
    if not _is_int(code) or not isinstance(message, str):
        return None
    if file is not None and not isinstance(file, str):
        return None
    if any(v is not None and not _is_int(v) for v in (start, end)):
        return None
    return TscDiagnostic(code, message, None if file is None else same_file_key(file),
                         start, end)


def _parse_response(raw: str, claimed: str) -> TscResult | None:
    """Everything the probe said, or None if any part of it cannot be vouched for."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("protocol") != PROTOCOL:
        return None
    version, files = data.get("version"), data.get("files")
    if not isinstance(version, str) or not version.split(".")[0].isdigit():
        return None
    if int(version.split(".")[0]) not in SUPPORTED_TS_MAJORS:
        return None
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return None
    keys = frozenset(same_file_key(f) for f in files)
    if same_file_key(claimed) not in keys:
        return None
    raw_diagnostics = data.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return None
    diagnostics = tuple(_diagnostic(d) for d in raw_diagnostics)
    if None in diagnostics:
        return None
    directives = data.get("directives")
    if not isinstance(directives, dict) or not isinstance(directives.get("nocheck"), bool):
        return None
    any_lines = _lines(data.get("any_lines"))
    assertion_lines = _lines(data.get("assertion_lines"))
    subscript_lines = _lines(data.get("subscript_lines"))
    directive_lines = _lines(directives.get("lines"))
    directive_targets = _lines(directives.get("targets"))
    if None in (any_lines, assertion_lines, subscript_lines, directive_lines, directive_targets):
        return None
    return TscResult(version, keys, diagnostics, any_lines, assertion_lines,
                     directive_lines, directive_targets, directives["nocheck"],
                     subscript_lines)


def run_tsc(path: str, span: tuple[int, int]) -> TscResult | None:
    """The TypeScript compiler's view of `path`, or None for "could not tell, escalate".

    The probe runs from the system temp directory with the typescript package taken
    from the global npm root: nothing in the audited tree is ever executed.
    """
    node = resolve_tool("node")
    package = typescript_package()
    if node is None or package is None:
        return None
    target = os.path.abspath(path)
    config, repo_root = _scan_up(target)
    if _is_inside(os.path.realpath(package), os.path.realpath(repo_root or os.getcwd())):
        return None
    request = {
        "protocol": PROTOCOL,
        "typescript": package,
        "tsconfig": config,
        "file": target,
        "span": list(span),
        "copy_options": list(COPY_OPTIONS),
        "forced_options": FORCED_OPTIONS,
    }
    try:
        done = subprocess.run([node, PROBE], input=json.dumps(request), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              cwd=tempfile.gettempdir(), timeout=TS_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0:
        return None
    return _parse_response(done.stdout, target)
