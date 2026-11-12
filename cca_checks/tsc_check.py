import functools
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
    # Each decides whether the repo's own build is clean: without moduleDetection a
    # forced module is checked as a global script, and without the import extension
    # pair every `import "./h.ts"` is a TS5097 that blocks the whole file. noEmit is
    # forced, which both extension options require.
    "moduleDetection", "allowImportingTsExtensions", "rewriteRelativeImportExtensions",
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
    # Lines where a name's declaration carries a definite assignment `!`.
    definite_assignment_lines: frozenset[int]
    # A declared @types package is not installed, so globals it declares may be missing.
    types_incomplete: bool
    missing_types: tuple[str, ...] = ()
    # Lines of `import "x";` (no import clause) in the claimed file.
    side_effect_import_lines: frozenset[int] = frozenset()
    # Some program file has an unresolved `import "pkg";` (a non-relative specifier).
    unresolved_package_side_effect_import: bool = False


def same_file_key(path: str) -> str:
    # Absolute first: a cached relative path would outlive a change of directory.
    return _absolute_file_key(os.path.abspath(path))


# realpath costs a millisecond or two per call on Windows and _parse_response makes
# one per program file; one claim per process bounds the cache.
@functools.cache
def _absolute_file_key(path: str) -> str:
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


def unsupported_typescript(package: str) -> str | None:
    """Why the package at `package` cannot run the probe, or None when its major is supported.

    Read from its package.json before anything is launched: a TypeScript 7 install
    has one but no JavaScript compiler API, so the probe would refuse every claim
    while capabilities reported TypeScript available.
    """
    try:
        with open(os.path.join(package, "package.json"), encoding="utf-8") as f:
            version = json.load(f).get("version")
    except (OSError, ValueError, AttributeError):
        version = None
    if not isinstance(version, str) or not version.split(".")[0].isdigit():
        return f"the global typescript at {package} has no readable version"
    if int(version.split(".")[0]) not in SUPPORTED_TS_MAJORS:
        majors = ", ".join(map(str, sorted(SUPPORTED_TS_MAJORS)))
        return f"global typescript {version} is not a supported major (supported: {majors})"
    return None


_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "peerDependencies")


def _missing_types(path: str, repo_root: str | None) -> tuple[bool, tuple[str, ...]]:
    """(incomplete, names): a declared `@types/<name>` that is not installed.

    Walks from the file's directory to the .git root, or to the filesystem root when
    there is none: an unpacked tarball has no .git, and its package.json sits above
    the source directory. A package.json that does not parse, or has a dependency
    section that is not an object, counts as incomplete: its declarations cannot be
    checked, and an absent global must not read as an undefined name.
    """
    start = os.path.dirname(os.path.abspath(path))
    chain = [start]
    while chain[-1] != repo_root and os.path.dirname(chain[-1]) != chain[-1]:
        chain.append(os.path.dirname(chain[-1]))
    missing: list[str] = []
    for directory in chain:
        manifest = os.path.join(directory, "package.json")
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
            sections = [data.get(section, {}) for section in _DEPENDENCY_SECTIONS]
        except (OSError, ValueError, AttributeError):
            return True, ()
        # JSON object keys are always strings, so an object section needs no key check.
        if not all(isinstance(section, dict) for section in sections):
            return True, ()
        declared = [name for section in sections for name in section]
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
    if not isinstance(code, int) or not _is_int(code) or not isinstance(message, str):
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
    parsed = [_diagnostic(d) for d in raw_diagnostics]
    diagnostics = tuple(d for d in parsed if d is not None)
    if len(diagnostics) != len(parsed):
        return None
    directives = data.get("directives")
    if not isinstance(directives, dict) or not isinstance(directives.get("nocheck"), bool):
        return None
    any_lines = _lines(data.get("any_lines"))
    assertion_lines = _lines(data.get("assertion_lines"))
    subscript_lines = _lines(data.get("subscript_lines"))
    definite_lines = _lines(data.get("definite_assignment_lines"))
    directive_lines = _lines(directives.get("lines"))
    directive_targets = _lines(directives.get("targets"))
    side_effect_lines = _lines(data.get("side_effect_import_lines"))
    package_import = data.get("unresolved_package_side_effect_import")
    if (any_lines is None or assertion_lines is None or subscript_lines is None
            or definite_lines is None or directive_lines is None or directive_targets is None
            or side_effect_lines is None or not isinstance(package_import, bool)):
        return None
    return TscResult(version, keys, diagnostics, any_lines, assertion_lines,
                     directive_lines, directive_targets, directives["nocheck"],
                     subscript_lines, definite_lines, *missing,
                     side_effect_import_lines=side_effect_lines,
                     unresolved_package_side_effect_import=package_import)


def run_tsc(path: str, span: tuple[int, int]) -> TscResult | None:
    """The TypeScript compiler's view of `path`, or None for "could not tell, escalate".

    The probe runs from the system temp directory with the typescript package taken
    from the global npm root: nothing in the audited tree is ever executed.
    """
    node = resolve_tool("node")
    package = typescript_package()
    if node is None or package is None or unsupported_typescript(package) is not None:
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
NULLABILITY_CODES = frozenset({2531, 2532, 2533, 18047, 18048, 18049, 2721, 2722, 2723})
# 2739 and 2740 missing properties, 2559 no properties in common, 2352 a conversion that
# may be a mistake, 2416 a member not assignable to the base's, 2420 a class that does
# not implement its interface: assignability siblings of 2322 (messages read from the
# 6.0.3 lib).
TYPE_CODES = frozenset({2322, 2345, 2339, 2741, 2769, 2353, 2551, 2362, 2363, 2365, 2367,
                        2349, 2554, 2555, 2739, 2740, 2559, 2352, 2416, 2420})
# 2882 is an unresolved side-effect import, 2875 a JSX runtime module that cannot be
# found, 2792 the moduleResolution form of 2307: each a module the machine lacks.
ENVIRONMENT_CODES = frozenset({2307, 7016, 2688, 2580, 2581, 2582, 2583, 2584, 2591, 2592,
                               2593, 2867, 2868, 2882, 2875, 2792})
# The implicit any family and `unknown`: the compiler was blind, so silence proves nothing.
BLINDNESS_CODES = frozenset({7005, 7006, 7008, 7010, 7011, 7015, 7019, 7031, 7034, 7053,
                             18046})
# An import names a member its module does not export (or no default export). The
# binding still resolves, as the error type, so its uses report no definedness code
# anywhere. Its own set, not ENVIRONMENT_CODES: it may be a real defect at the import
# line rather than a fact about the machine, and `type` and `nullability` claims
# already see the binding as `any` through any_lines, so blocking them would only cost
# refutations.
IMPORT_MEMBER_CODES = frozenset({2305, 2724, 2459, 2460, 2614, 1192, 2613})
# "Do you need to install type definitions for ...": a type package is missing, so a
# global it declares may be what another line reports as undefined. 2581 is the jQuery
# form of 2580; 2867 and 2868 are the Bun forms.
INSTALL_TYPES_CODES = frozenset({2580, 2581, 2582, 2591, 2592, 2593, 2867, 2868})
CODES_BY_CLAIM = {"definedness": DEFINEDNESS_CODES, "nullability": NULLABILITY_CODES,
                  "type": TYPE_CODES}
MISSING_TYPE_PACKAGE = 2688
UNRESOLVED_SIDE_EFFECT_IMPORT = 2882
UNRESOLVED_MODULE_CODES = frozenset({2307, 2792})


def is_environment(code: int) -> bool:
    return code in ENVIRONMENT_CODES or 5000 <= code <= 5999


def is_syntactic(code: int) -> bool:
    return 1000 <= code <= 1999


_LISTED_CODES = (DEFINEDNESS_CODES | NULLABILITY_CODES | TYPE_CODES | BLINDNESS_CODES
                 | IMPORT_MEMBER_CODES)


def _unlisted(code: int) -> bool:
    """A code no set names: possibly a sibling of the claimed defect nobody catalogued."""
    return code not in _LISTED_CODES and not is_syntactic(code) and not is_environment(code)


def _covers(d: TscDiagnostic, line: int) -> bool:
    return d.start_line is not None and d.end_line is not None and (
        d.start_line <= line <= d.end_line)


def _overlaps(d: TscDiagnostic, span: tuple[int, int]) -> bool:
    return d.start_line is not None and d.end_line is not None and (
        d.start_line <= span[1] and d.end_line >= span[0])


def _in_span(lines: frozenset[int], span: tuple[int, int]) -> bool:
    return any(span[0] <= n <= span[1] for n in lines)


def verdict_for_tsc(claim: Claim, result: TscResult | None, codes: frozenset[int],
                    span: tuple[int, int] | None, *,
                    wide_span: tuple[int, int] | None = None) -> Verdict:
    """Settle a claim against the compiler's diagnostics. Three-way, never false-refutes.

    `wide_span`, the statement enclosing the claim's function, is read only by the off
    by a line and directive checks: tsc places a callback's error on the call, and a
    directive goes above the call, both outside the callback. It defaults to `span`.
    """
    if wide_span is None:
        wide_span = span
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
            install = next((d for d in result.diagnostics if d.code in INSTALL_TYPES_CODES), None)
            if install:
                return tsc("UNCERTAIN", f"TS{hit.code} @ {where} but the program has a "
                                        f"TS{install.code} (install type definitions); escalated")
            if result.types_incomplete:
                names = ", ".join(result.missing_types) or "an unreadable package.json"
                return tsc("UNCERTAIN", f"TS{hit.code} @ {where} but a declared type package is "
                                        f"not installed ({names}); escalated")
        if claim.claim_type in ("type", "definedness"):
            # An unresolved side-effect import may be what declares or augments the
            # global this line reports on, so the error may be the machine's.
            unresolved = next((d for d in here if d.code == UNRESOLVED_SIDE_EFFECT_IMPORT or (
                d.code in UNRESOLVED_MODULE_CODES
                and d.start_line in result.side_effect_import_lines)), None)
            if unresolved:
                return tsc("UNCERTAIN", f"TS{hit.code} @ {where} but {claim.file} has an "
                                        f"unresolved side-effect import (TS{unresolved.code} at "
                                        f"line {unresolved.start_line}); escalated")
            if result.unresolved_package_side_effect_import:
                return tsc("UNCERTAIN", f"TS{hit.code} @ {where} but the program has an "
                                        f"unresolved side-effect import of a package, which may "
                                        f"augment the global this line reports on; escalated")
        return tsc("CONFIRMED", f"tsc TS{hit.code} @ {where}: {hit.message}")

    if at_line:
        seen = ", ".join(f"TS{c}" for c in sorted({d.code for d in at_line}))
        return tsc("UNCERTAIN", f"tsc reported {len(at_line)} diagnostic(s) @ {where} but none "
                                f"in the expected code set (saw: {seen}); escalated")

    # From here on the question is whether silence is believable.
    if result.nocheck or (wide_span is not None
                          and _in_span(result.directive_targets, wide_span)):
        return tsc("UNCERTAIN", f"a @ts-nocheck or @ts-ignore / @ts-expect-error directive "
                                f"covers the enclosing scope of {where}; escalated")

    if span is None or wide_span is None:
        return tsc("UNCERTAIN", f"no enclosing span for {where}; a refutation needs one")

    label = claim.claim_type
    # An unlisted code counts too, so a sibling of the claimed defect escalates.
    nearby = [d for d in here if (d.code in codes or _unlisted(d.code))
              and _overlaps(d, wide_span)]
    if nearby:
        lines = ", ".join(sorted({str(d.start_line) for d in nearby}))
        return tsc("UNCERTAIN", f"tsc reported no {label} diagnostic exactly at {where}, but did "
                                f"report one in the enclosing scope (lines {wide_span[0]}-"
                                f"{wide_span[1]}, at {lines}); the claim may be off by a line "
                                f"rather than false; escalated")

    if any(d.file is None or (d.file == key and (
            is_environment(d.code) or d.start_line is None or d.end_line is None))
           for d in result.diagnostics):
        # Includes diagnostics with no file, or no line in the claimed file: they cannot
        # be placed, so silence at the claim is not proof. An unresolved import makes its
        # bindings any.
        return tsc("UNCERTAIN", f"tsc reported an environment or configuration diagnostic "
                                f"affecting {claim.file}; escalated")

    if claim.claim_type == "definedness":
        if any(d.code in IMPORT_MEMBER_CODES for d in here):
            return tsc("UNCERTAIN", f"{claim.file} imports a member its module does not export, "
                                    f"and uses of that name report nothing; escalated")
        if _in_span(result.definite_assignment_lines, span):
            # `let n!: number` tells the checker n is assigned, so TS2454 cannot fire.
            return tsc("UNCERTAIN", f"the enclosing scope of {where} uses a name declared with "
                                    f"a definite assignment `!`; escalated")

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

    # Last, so a more specific reason (1192 is an import member error) speaks first.
    syntax = next((d for d in here if is_syntactic(d.code) and d.start_line is not None), None)
    if syntax:
        return tsc("UNCERTAIN", f"tsc reported a TS1xxx syntax or grammar error (TS{syntax.code} "
                                f"at line {syntax.start_line}) in {claim.file}, so the file may "
                                f"not have been read as written; escalated")

    return tsc("FALSE_POSITIVE", f"tsc: no {label} diagnostic (TS codes "
                                 f"{', '.join(map(str, sorted(codes)))}) @ {where} or in lines "
                                 f"{span[0]}-{span[1]}")
