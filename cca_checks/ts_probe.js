"use strict";
/*
 * The TypeScript type probe. CCA's Python side (cca_checks/tsc_check.py) launches
 * `node ts_probe.js` with one JSON request on stdin and reads one JSON response on
 * stdout. The request names the typescript package to load by absolute path; nothing
 * else outside the Node standard library is required, and nothing from the audited
 * tree is ever executed: its tsconfig files are read as data only.
 *
 * Request:  {"protocol": 1, "typescript": <dir>, "tsconfig": <path or null>,
 *            "file": <path>, "span": [lo, hi], "copy_options": [<names>],
 *            "forced_options": {<tsconfig style JSON>}}
 * Response: {"protocol": 1, "version", "files", "diagnostics", "any_lines",
 *            "directives": {"lines", "nocheck"}, "subscript_lines"}, exit 0.
 * Refusal:  exit 2, one line on stderr, nothing on stdout.
 *
 * Soundness rule: no repo option, suppression comment or `any` may make the compiler
 * look silent about code it could not see. Hence only `copy_options` keys survive from
 * the repo config, the forced options always win, the claimed file is always a root,
 * and a config the compiler reports an error on is refused rather than half applied.
 *
 * Diagnostic codes observed with TypeScript 6.0.3 (TS_PIN), node v22.11.0, on
 * tests/fixtures/typescript (tests/test_ts_probe.py):
 *
 *   code   fixture                         defect
 *   2304   basic/undefined_name.ts:2       Cannot find name 'RISK_CAP'
 *   18047  basic/null_access.ts:3          'u' is possibly 'null'
 *   18047  weak/null_access.ts:3           same, under a repo `strict: false`
 *   18047  nocheck_config/null_access.ts:3 same, under a repo `noCheck: true`
 *   18047  solution/src/main.ts:4          same, through the owning reference
 *   18047  tsx/component.tsx:3             same, in JSX
 *   2345   basic/bad_arg.ts:5              string is not assignable to number
 *   7006   basic/implicit_any.ts:1         parameter 'u' implicitly has an 'any' type
 *   7006   weak/implicit.ts:1              same, under a repo `noImplicitAny: false`
 *   2307   basic/missing_module.ts:1       Cannot find module './nope'
 *   2688   types_node (unlocated)          Cannot find type definition file for 'node'
 *   2591   types_node/uses_process.ts:2    Cannot find name 'process' (install @types/node)
 *   2304   autotypes/uses_global.ts:2      global from node_modules/@types not in scope:
 *                                          TypeScript 6 defaults `types` to [], so no
 *                                          @types package is included unless listed
 *   5101   deprecated (unlocated)          'baseUrl' is deprecated
 *   5107   deprecated (unlocated)          'moduleResolution=node10', 'target=ES5' and
 *                                          'esModuleInterop=false' are deprecated
 *   2532   weak/index_access.ts:3          Object is possibly 'undefined', only if the
 *                                          repo's noUncheckedIndexedAccess were applied
 *   1136   broken/tsconfig.json            JSON syntax error; the probe refuses
 *   5023   (scratch config)                Unknown compiler option; the probe refuses
 *
 * The 5101 and 5107 errors appear only when `ignoreDeprecations` is not forced; with
 * the forced `"6.0"` they are silenced, which silences no check.
 *
 * Directives. Comments are read from the parser's own tokens, so a string, template or
 * regular expression literal is never mistaken for a comment. A comment that merely
 * contains `@ts-ignore` or `@ts-expect-error` is reported, because TypeScript 6.0.3
 * honours `/// @ts-ignore`, `/* @ts-ignore *\/` and `// @ts-ignored` as well as the
 * plain form; over reporting costs a refutation, under reporting would buy one.
 */

const fs = require("fs");
const path = require("path");

const PROTOCOL = 1;

function refuse(message) {
  // writeSync, because process.exit does not wait for an asynchronous stderr write.
  fs.writeSync(2, "ts_probe: " + String(message).replace(/\s+/g, " ").trim() + "\n");
  process.exit(2);
}

function isStringArray(value) {
  return Array.isArray(value) && value.every((v) => typeof v === "string");
}

function readRequest() {
  let request;
  try {
    request = JSON.parse(fs.readFileSync(0, "utf8"));
  } catch (err) {
    refuse("request is not JSON: " + err.message);
  }
  const ok = request !== null && typeof request === "object"
    && request.protocol === PROTOCOL
    && typeof request.typescript === "string"
    && (request.tsconfig === null || typeof request.tsconfig === "string")
    && typeof request.file === "string"
    && Array.isArray(request.span) && request.span.length === 2
    && request.span.every(Number.isInteger)
    && isStringArray(request.copy_options)
    && request.forced_options !== null && typeof request.forced_options === "object"
    && !Array.isArray(request.forced_options);
  if (!ok) refuse("request does not match protocol " + PROTOCOL);
  return request;
}

function samePathKey(p) {
  const resolved = path.resolve(p);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function firstError(ts, diagnostics) {
  const errors = diagnostics.filter((d) => d.category === ts.DiagnosticCategory.Error);
  if (errors.length === 0) return null;
  const d = errors[0];
  return "TS" + d.code + " " + ts.flattenDiagnosticMessageText(d.messageText, " ");
}

function parseConfig(ts, configPath) {
  // The config parser asserts its file names use "/"; a Windows path with "\" fails a
  // Debug assertion as soon as there is a JSON error to attach to the file.
  configPath = path.resolve(configPath).split(path.sep).join("/");
  let unrecoverable = null;
  const host = Object.assign({}, ts.sys, {
    onUnRecoverableConfigFileDiagnostic: (d) => { unrecoverable = d; },
  });
  const parsed = ts.getParsedCommandLineOfConfigFile(configPath, undefined, host);
  if (unrecoverable) refuse(configPath + ": " + firstError(ts, [unrecoverable]));
  if (!parsed) refuse(configPath + ": could not be read");
  // The JSON parser recovers from syntax errors and keeps them out of parsed.errors;
  // this is the list tsc itself prints for a config.
  const error = firstError(ts, ts.getConfigFileParsingDiagnostics(parsed));
  if (error) refuse(configPath + ": " + error);
  return parsed;
}

// A solution config (`"files": []` plus `references`) owns nothing itself: the claimed
// file belongs to exactly one of its references, or the probe cannot say which options
// apply to it.
function owningConfig(ts, configPath, target) {
  const parsed = parseConfig(ts, configPath);
  const refs = parsed.projectReferences || [];
  if (parsed.fileNames.length > 0 || refs.length === 0) return parsed;
  const key = samePathKey(target);
  const owners = refs
    .map((ref) => parseConfig(ts, ts.resolveProjectReferencePath(ref)))
    .filter((p) => p.fileNames.some((f) => samePathKey(f) === key));
  if (owners.length !== 1) {
    refuse(configPath + ": " + owners.length + " project references own " + target);
  }
  return owners[0];
}

function effectiveOptions(ts, request, repo, basePath) {
  const forced = ts.convertCompilerOptionsFromJson(request.forced_options, basePath);
  const error = firstError(ts, forced.errors);
  if (error) refuse("forced_options: " + error);
  const options = {};
  if (repo) {
    for (const name of request.copy_options) {
      if (Object.prototype.hasOwnProperty.call(repo.options, name)) {
        options[name] = repo.options[name];
      }
    }
    // Two values the parser derives from the config's location rather than from a
    // key: `paths` without `baseUrl` resolves against pathsBasePath, and automatic
    // @types inclusion starts from configFilePath. Without them both would silently
    // resolve against the probe's working directory instead.
    if (options.paths !== undefined) options.pathsBasePath = repo.options.pathsBasePath;
    options.configFilePath = repo.options.configFilePath;
  }
  return Object.assign(options, forced.options);
}

function lineOf(sourceFile, position) {
  return sourceFile.getLineAndCharacterOfPosition(position).line + 1;
}

function serializeDiagnostic(ts, d) {
  const located = d.file !== undefined && d.start !== undefined;
  return {
    code: d.code,
    message: ts.flattenDiagnosticMessageText(d.messageText, "\n"),
    file: d.file ? path.resolve(d.file.fileName) : null,
    start_line: located ? lineOf(d.file, d.start) : null,
    end_line: located ? lineOf(d.file, d.start + (d.length || 0)) : null,
  };
}

// `<div>` names no value, so its tag has the error type, which carries the Any flag;
// counting it would mark every line of JSX as blind. A component tag (`<Card>`) is a
// value and is still checked.
function isIntrinsicTagName(ts, node) {
  const parent = node.parent;
  const isTag = (ts.isJsxOpeningLikeElement(parent) || ts.isJsxClosingElement(parent))
    && parent.tagName === node;
  return isTag && /^[a-z]|-/.test(node.text);
}

function expressionLines(ts, checker, sourceFile, span) {
  const [lo, hi] = span;
  const anyLines = new Set();
  const subscriptLines = new Set();
  const typed = new Set([
    ts.SyntaxKind.Identifier,
    ts.SyntaxKind.PropertyAccessExpression,
    ts.SyntaxKind.ElementAccessExpression,
    ts.SyntaxKind.CallExpression,
  ]);
  function visit(node) {
    if (typed.has(node.kind) && !(ts.isIdentifier(node) && isIntrinsicTagName(ts, node))) {
      const line = lineOf(sourceFile, node.getStart(sourceFile));
      if (line >= lo && line <= hi) {
        if (checker.getTypeAtLocation(node).flags & ts.TypeFlags.Any) anyLines.add(line);
        if (node.kind === ts.SyntaxKind.ElementAccessExpression) subscriptLines.add(line);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return { anyLines, subscriptLines };
}

function isJSDoc(ts, node) {
  return node.kind >= ts.SyntaxKind.FirstJSDocNode && node.kind <= ts.SyntaxKind.LastJSDocNode;
}

// Every comment is leading trivia of exactly one token, so collecting the leading
// comment ranges of each token the parser produced finds every comment and nothing
// inside a literal. A bare scanner over the text cannot do this: it does not know
// that `/`...`/` is a regular expression, and loses track after a backtick inside one.
function commentDirectives(ts, sourceFile) {
  const text = sourceFile.text;
  const seen = new Set();
  const lines = new Set();
  let nocheck = false;
  function token(node) {
    for (const range of ts.getLeadingCommentRanges(text, node.pos) || []) {
      if (seen.has(range.pos)) continue;
      seen.add(range.pos);
      const comment = text.slice(range.pos, range.end);
      if (/@ts-(ignore|expect-error)/.test(comment)) {
        for (let l = lineOf(sourceFile, range.pos); l <= lineOf(sourceFile, range.end); l++) {
          lines.add(l);
        }
      }
      if (/@ts-nocheck/.test(comment)) nocheck = true;
    }
  }
  function walk(node) {
    if (isJSDoc(ts, node)) return;
    const children = node.getChildren(sourceFile);
    if (children.length === 0) token(node);
    else children.forEach(walk);
  }
  walk(sourceFile);
  return { lines: [...lines].sort((a, b) => a - b), nocheck };
}

function main() {
  const request = readRequest();
  let ts;
  try {
    ts = require(path.resolve(request.typescript));
  } catch (err) {
    refuse("cannot load typescript from " + request.typescript + ": " + err.message);
  }
  if (typeof ts.createProgram !== "function") {
    refuse(request.typescript + " has no compiler API (createProgram)");
  }

  const target = path.resolve(request.file);
  const repo = request.tsconfig === null
    ? null
    : owningConfig(ts, path.resolve(request.tsconfig), target);
  const basePath = repo ? path.dirname(repo.options.configFilePath) : path.dirname(target);
  const options = effectiveOptions(ts, request, repo, basePath);
  const rootNames = (repo ? repo.fileNames : []).concat([target]);

  const host = ts.createCompilerHost(options);
  const program = ts.createProgram({ rootNames, options, host });
  const sourceFile = program.getSourceFile(target);
  if (!sourceFile) refuse(target + " is not in the program");

  // Diagnostics first: a type query on a name the checker never resolved during the
  // check (a JSX tag, for one) adds a "Cannot find name" of its own, which would read
  // as a defect in the audited code.
  const diagnostics = ts.getPreEmitDiagnostics(program).map((d) => serializeDiagnostic(ts, d));
  const checker = program.getTypeChecker();
  const { anyLines, subscriptLines } = expressionLines(ts, checker, sourceFile, request.span);
  const response = {
    protocol: PROTOCOL,
    version: ts.version,
    files: program.getSourceFiles().map((f) => path.resolve(f.fileName)),
    diagnostics,
    any_lines: [...anyLines].sort((a, b) => a - b),
    directives: commentDirectives(ts, sourceFile),
    subscript_lines: [...subscriptLines].sort((a, b) => a - b),
  };
  process.stdout.write(JSON.stringify(response) + "\n");
}

try {
  main();
} catch (err) {
  refuse("internal error: " + (err && err.stack ? err.stack : err));
}
