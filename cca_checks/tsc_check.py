import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from .claim import Claim, Verdict, make_verdict
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
    # A declared @types package is not installed, so globals it declares may be missing.
    types_incomplete: bool
    missing_types: tuple[str, ...] = ()


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


_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "peerDependencies")


def _missing_types(path: str, repo_root: str | None) -> tuple[bool, tuple[str, ...]]:
    """(incomplete, names): a declared `@types/<name>` that is not installed.

    Walks from the file's directory to the .git root (or only that directory when there
    is none). A package.json that does not parse counts as incomplete: its declarations
    cannot be checked, and an absent global must not read as an undefined name.
    """
    start = os.path.dirname(os.path.abspath(path))
    chain = [start]
    while repo_root is not None and chain[-1] != repo_root and (
            os.path.dirname(chain[-1]) != chain[-1]):
        chain.append(os.path.dirname(chain[-1]))
    missing: list[str] = []
    for directory in chain:
        manifest = os.path.join(directory, "package.json")
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
            declared = [name for section in _DEPENDENCY_SECTIONS
                        for name in (data.get(section) or {})]
        except (OSError, ValueError, AttributeError, TypeError):
            return True, ()
        reachable = chain[chain.index(directory):]
        for name in declared:
            if name.startswith("@types/") and not any(
                    os.path.isdir(os.path.join(d, "node_modules", name)) for d in reachable):
                missing.append(name)
    return bool(missing), tuple(sorted(set(missing)))


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


def _parse_response(raw: str, claimed: str, missing: tuple[bool, tuple[str, ...]]
                    ) -> TscResult | None:
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
                     subscript_lines, *missing)


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
    return _parse_response(done.stdout, target, _missing_types(target, repo_root))


# Diagnostic codes by role. Drift may cost a confirmation, never buy a refutation:
# a code outside every set at the claimed line escalates.
DEFINEDNESS_CODES = frozenset({2304, 2552, 2448, 2454})
NULLABILITY_CODES = frozenset({2531, 2532, 2533, 18047, 18048, 18049, 2722})
TYPE_CODES = frozenset({2322, 2345, 2339, 2741, 2769, 2353, 2551, 2362, 2363, 2365, 2367,
                        2349, 2554, 2555})
ENVIRONMENT_CODES = frozenset({2307, 7016, 2688, 2580, 2581, 2582, 2583, 2584, 2591, 2592,
                               2593})
# The implicit any family and `unknown`: the compiler was blind, so silence proves nothing.
BLINDNESS_CODES = frozenset({7005, 7006, 7008, 7010, 7011, 7015, 7019, 7031, 7034, 7053,
                             18046})
CODES_BY_CLAIM = {"definedness": DEFINEDNESS_CODES, "nullability": NULLABILITY_CODES,
                  "type": TYPE_CODES}
MISSING_TYPE_PACKAGE = 2688


def is_environment(code: int) -> bool:
    return code in ENVIRONMENT_CODES or 5000 <= code <= 5999


def _covers(d: TscDiagnostic, line: int) -> bool:
    return d.start_line is not None and d.end_line is not None and (
        d.start_line <= line <= d.end_line)


def _overlaps(d: TscDiagnostic, span: tuple[int, int]) -> bool:
    return d.start_line is not None and d.end_line is not None and (
        d.start_line <= span[1] and d.end_line >= span[0])


def _in_span(lines: frozenset[int], span: tuple[int, int]) -> bool:
    return any(span[0] <= n <= span[1] for n in lines)


def verdict_for_tsc(claim: Claim, result: TscResult | None, codes: frozenset[int],
                    span: tuple[int, int] | None) -> Verdict:
    """Settle a claim against the compiler's diagnostics. Three-way, never false-refutes."""
    def tsc(verdict: str, evidence: str) -> Verdict:
        return make_verdict(claim.finding_id, verdict, evidence, "tsc")

    if result is None:
        return make_verdict(claim.finding_id, "UNCERTAIN",
                            "tsc unavailable or could not analyze; falling back to LLM", "llm")

    where = f"{claim.file}:{claim.line}"
    key = same_file_key(claim.file)
    here = [d for d in result.diagnostics if d.file == key]
    at_line = [d for d in here if _covers(d, claim.line)]

    hit = next((d for d in at_line if d.code in codes), None)
    if hit:
        if claim.claim_type == "definedness":
            # A missing type package can remove globals it declares, so the name may exist.
            if any(d.code == MISSING_TYPE_PACKAGE for d in result.diagnostics):
                return tsc("UNCERTAIN", f"TS{hit.code} @ {where} but the program has a TS2688 "
                                        f"(missing type definition file); escalated")
            if result.types_incomplete:
                names = ", ".join(result.missing_types) or "an unreadable package.json"
                return tsc("UNCERTAIN", f"TS{hit.code} @ {where} but a declared type package is "
                                        f"not installed ({names}); escalated")
        return tsc("CONFIRMED", f"tsc TS{hit.code} @ {where}: {hit.message}")

    if at_line:
        seen = ", ".join(f"TS{c}" for c in sorted({d.code for d in at_line}))
        return tsc("UNCERTAIN", f"tsc reported {len(at_line)} diagnostic(s) @ {where} but none "
                                f"in the expected code set (saw: {seen}); escalated")

    # From here on the question is whether silence is believable.
    if result.nocheck or (span is not None and _in_span(result.directive_targets, span)):
        return tsc("UNCERTAIN", f"a @ts-nocheck or @ts-ignore / @ts-expect-error directive "
                                f"covers the enclosing scope of {where}; escalated")

    if span is None:
        return tsc("UNCERTAIN", f"no enclosing span for {where}; a refutation needs one")

    label = claim.claim_type
    nearby = [d for d in here if d.code in codes and _overlaps(d, span)]
    if nearby:
        lines = ", ".join(sorted({str(d.start_line) for d in nearby}))
        return tsc("UNCERTAIN", f"tsc reported no {label} diagnostic exactly at {where}, but did "
                                f"report one in the enclosing scope (lines {span[0]}-{span[1]}, "
                                f"at {lines}); the claim may be off by a line rather than "
                                f"false; escalated")

    if any(d.file is None or (d.file == key and (
            is_environment(d.code) or d.start_line is None or d.end_line is None))
           for d in result.diagnostics):
        # Includes diagnostics with no file, or no line in the claimed file: they cannot
        # be placed, so silence at the claim is not proof. An unresolved import makes its
        # bindings any.
        return tsc("UNCERTAIN", f"tsc reported an environment or configuration diagnostic "
                                f"affecting {claim.file}; escalated")

    if claim.claim_type == "nullability" and _in_span(result.subscript_lines, span):
        # noUncheckedIndexedAccess is not forced, so an index access is never proven safe;
        # the dereference may sit on another line of the scope than the one cited.
        return tsc("UNCERTAIN", f"the enclosing scope of {where} holds an element access or "
                                f"index signature read, which tsc does not check for "
                                f"undefined here; escalated")

    if claim.claim_type in ("type", "nullability"):
        blind = [d for d in here if d.code in BLINDNESS_CODES and _overlaps(d, span)]
        if blind or _in_span(result.any_lines, span) or _in_span(result.assertion_lines, span):
            return tsc("UNCERTAIN", f"tsc may not see the real types in the enclosing scope "
                                    f"(lines {span[0]}-{span[1]}: any, assertion or implicit "
                                    f"any) @ {where}; escalated")

    return tsc("FALSE_POSITIVE", f"tsc: no {label} diagnostic (TS codes "
                                 f"{', '.join(map(str, sorted(codes)))}) @ {where} or in lines "
                                 f"{span[0]}-{span[1]}")
