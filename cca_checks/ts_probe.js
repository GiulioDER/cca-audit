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
 *            "assertion_lines", "directives": {"lines", "targets", "nocheck"},
 *            "subscript_lines", "definite_assignment_lines"}, exit 0.
 *           A line is an assertion, subscript or definite assignment line either for
 *           what is written on it or for what a name on it resolves to: a name whose
 *           declaration, in or outside the span, is initialized by an assertion or an
 *           index read, or carries a definite assignment `!`.
 * Refusal:  exit 2, one line on stderr, nothing on stdout. The claimed file being a
 *           declaration file is a refusal too: skipLibCheck hides its errors.
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
 *                                          @types package is included unless listed;
 *                                          gone once the probe sets `types: ["*"]` for
 *                                          a config without `types`
 *   2304   types_empty/uses_global.ts:2    same, under the repo's explicit `types: []`
 *   5101   deprecated (unlocated)          'baseUrl' is deprecated
 *   5107   deprecated (unlocated)          'moduleResolution=node10', 'target=ES5' and
 *                                          'esModuleInterop=false' are deprecated
 *   2532   weak/index_access.ts:3          Object is possibly 'undefined', only if the
 *                                          repo's noUncheckedIndexedAccess were applied
 *   18047  outside_span/outer_bang_ctl.ts:4 'app' is possibly 'null'; gone under the
 *                                          module level `!` of outer_bang.ts
 *   2454   outside_span/definite_ctl.ts:3  'n' is used before being assigned; gone
 *                                          under `let n!` in definite.ts
 *   2304   module_detection/b.ts:2         'helper' undefined only under the repo's
 *                                          `moduleDetection: "force"`
 *   5097   ts_extensions/main.ts:1         `./h.ts` import, only if the repo's
 *                                          allowImportingTsExtensions were dropped
 *   2305   import_member/use.ts:1          no exported member 'nope'; nothing at line 3
 *   1192   import_member/use_default.ts:1  no default export; nothing at line 3
 *   2580   install_types/uses_dirname.ts:4 'process', install @types/node; line 2
 *                                          has a 2304 for '__dirname'
 *   1136   broken/tsconfig.json            JSON syntax error; the probe refuses
 *   5023   (scratch config)                Unknown compiler option; the probe refuses
 *
 * The 5101 and 5107 errors appear only when `ignoreDeprecations` is not forced; with
 * the forced `"6.0"` they are silenced, which silences no check.
 *
 * Directives. Comments are read as the leading and trailing comment ranges of the
 * parser's own tokens, so a string, template or regular expression literal is never
 * mistaken for a comment, and a comment after code on the same line
 * (`x = 1; // @ts-ignore`) is found as well as one on a line of its own. A comment that
 * merely contains `@ts-ignore` or `@ts-expect-error` is reported, because TypeScript
 * 6.0.3 honours `/// @ts-ignore`, `/* @ts-ignore *\/` and `// @ts-ignored` as well as
 * the plain form; over reporting costs a refutation, under reporting would buy one.
 * `targets` holds the line each one suppresses, found the way tsc finds it: the first
 * line after the comment that is neither blank nor a `//` comment.
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

function ancestorTypeRoots(file) {
  const roots = [];
  for (let dir = path.dirname(file); ; dir = path.dirname(dir)) {
    const candidate = path.join(dir, "node_modules", "@types");
    if (fs.existsSync(candidate)) roots.push(candidate);
    if (path.dirname(dir) === dir) return roots;
  }
}

function effectiveOptions(ts, request, repo, basePath, target) {
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
    // TypeScript 6 defaults `types` to [], which drops every global an installed
    // @types package declares: a repo built with TypeScript 5 never saw that, and its
    // globals would read as undefined names. "*" is the TypeScript 5 default. A
    // `types` the repo does set, [] included, is its own choice and is copied as is.
    if (repo.options.types === undefined) options.types = ["*"];
  } else {
    // No repo config at all: nothing to copy, so the same TypeScript 5 default.
    options.types = ["*"];
    // Without a config TypeScript looks for @types from the probe's working
    // directory, which is outside the audited tree; look from the claimed file.
    options.typeRoots = ancestorTypeRoots(target);
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

// `u!`, `u as T` and `<T>u` tell the checker what to believe, so its silence there is
// the author's word, not a proof. `as const` only narrows a literal and is not one.
function isAssertion(ts, node) {
  if (ts.isNonNullExpression(node)) return true;
  return (ts.isAsExpression(node) || ts.isTypeAssertionExpression(node))
    && !ts.isConstTypeReference(node.type);
}

// The member access, call or `!` chain an element access is the head of: in
// `a[i]` followed by `.x` on the next line, the possibly undefined value is
// dereferenced on that next line.
function accessChainEnd(ts, node) {
  let top = node;
  for (let p = top.parent; p && p.expression === top
    && (ts.isPropertyAccessExpression(p) || ts.isElementAccessExpression(p)
      || ts.isCallExpression(p) || ts.isNonNullExpression(p)); p = top.parent) {
    top = p;
  }
  return top.getEnd();
}

// Nodes the checker types as `any` although no value flows through them: a name in a
// type (`const` in `as const`, `NS` in `NS.T`) and a module specifier string. A value
// that does carry `any` is still caught at its own expression.
function namesNoValue(ts, node) {
  const parent = node.parent;
  if (ts.isIdentifier(node)) {
    return isIntrinsicTagName(ts, node)
      || ts.isTypeReferenceNode(parent) || ts.isQualifiedName(parent);
  }
  return ts.isStringLiteral(node)
    && (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent))
    && parent.moduleSpecifier === node;
}

// `m.alice` on a `Record<string, T>` reads through an index signature exactly like
// `m["alice"]`: undefined at run time, never reported without noUncheckedIndexedAccess.
function readsIndexSignature(ts, checker, node) {
  if (!ts.isPropertyAccessExpression(node)) return false;
  return indexSignatureOnly(checker, checker.getTypeAtLocation(node.expression), node.name.text);
}

function indexSignatureOnly(checker, type, name) {
  const apparent = checker.getApparentType(checker.getNonNullableType(type));
  return !checker.getPropertyOfType(apparent, name)
    && checker.getIndexInfosOfType(apparent).length > 0;
}

// The key a destructuring element reads, or null when it is computed (`{ [k]: v }`),
// which is an index read whatever the type says.
function literalKey(ts, name) {
  return ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)
    || ts.isNoSubstitutionTemplateLiteral(name) ? name.text : null;
}

// Destructuring reads by position or by key, so `const [u] = items`, `[u] = items` and
// `const { alice } = m` on a Record are the same unchecked read as `items[0]` and
// `m["alice"]`. A tuple position below the fixed length is a declared element and is
// checked, which keeps `const [s, setS] = useState()` and the pairs of
// `Object.entries` refutable; a position in a rest element (`[A, ...B[]]`) is not.
// `index` is the element's position in an array pattern, `key` its key in an object
// pattern (undefined for an array pattern, null when computed).
function destructuringReadsByIndex(ts, checker, sourceType, index, key) {
  if (key === undefined) {
    return !(checker.isTupleType(sourceType) && index < sourceType.target.fixedLength);
  }
  return key === null || indexSignatureOnly(checker, sourceType, key);
}

// A binding element (`u` in `const [u] = items`, `alice` in `const { alice } = m`)
// that reads through an index. Rest elements collect an array or object, never
// undefined.
function bindingReadsByIndex(ts, checker, element) {
  if (!ts.isBindingElement(element) || element.dotDotDotToken) return false;
  const pattern = element.parent;
  const sourceType = checker.getTypeAtLocation(pattern);
  if (ts.isArrayBindingPattern(pattern)) {
    return destructuringReadsByIndex(ts, checker, sourceType, pattern.elements.indexOf(element));
  }
  return destructuringReadsByIndex(ts, checker, sourceType, -1,
    literalKey(ts, element.propertyName || element.name));
}

// An array or object literal that is the target of a destructuring assignment, in
// exactly the positions checker.getTypeOfAssignmentPattern accepts (it asserts on any
// other parent).
function isAssignmentPattern(ts, node) {
  if (!ts.isArrayLiteralExpression(node) && !ts.isObjectLiteralExpression(node)) return false;
  const parent = node.parent;
  if (ts.isBinaryExpression(parent)) {
    return parent.left === node && parent.operatorToken.kind === ts.SyntaxKind.EqualsToken;
  }
  if (ts.isForOfStatement(parent)) return parent.initializer === node;
  if (ts.isArrayLiteralExpression(parent)) return isAssignmentPattern(ts, parent);
  if (ts.isPropertyAssignment(parent)) {
    return parent.initializer === node && isAssignmentPattern(ts, parent.parent);
  }
  return false;
}

// The targets of an assignment pattern that read through an index.
function assignmentTargetsByIndex(ts, checker, pattern) {
  const sourceType = checker.getTypeOfAssignmentPattern(pattern);
  if (ts.isArrayLiteralExpression(pattern)) {
    return pattern.elements.filter((e, i) => !ts.isOmittedExpression(e) && !ts.isSpreadElement(e)
      && destructuringReadsByIndex(ts, checker, sourceType, i));
  }
  return pattern.properties.filter((p) => (ts.isShorthandPropertyAssignment(p)
    || ts.isPropertyAssignment(p))
    && destructuringReadsByIndex(ts, checker, sourceType, -1, literalKey(ts, p.name)));
}

function skipParentheses(ts, node) {
  while (ts.isParenthesizedExpression(node)) node = node.expression;
  return node;
}

// What the declaration a name resolves to tells the checker to believe, wherever that
// declaration is: `const app = el!` at module level makes `app.x` inside a function
// look safe, and `let n!: number` makes `n` look assigned. This finds the declaration
// (undefined when the name has none); declarationFacts reads it.
function declarationOf(ts, checker, identifier) {
  const parent = identifier.parent;
  let symbol = ts.isShorthandPropertyAssignment(parent) && parent.name === identifier
    ? checker.getShorthandAssignmentValueSymbol(parent)
    : checker.getSymbolAtLocation(identifier);
  if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
  return symbol ? symbol.valueDeclaration : undefined;
}

function declarationFacts(ts, checker, declaration) {
  if (!declaration) return { asserted: false, definite: false, indexed: false };
  const initializer = declaration.initializer && skipParentheses(ts, declaration.initializer);
  const definite = declaration.exclamationToken !== undefined
    && (ts.isVariableDeclaration(declaration) || ts.isPropertyDeclaration(declaration));
  return {
    asserted: definite || (initializer !== undefined && isAssertion(ts, initializer)),
    definite,
    indexed: bindingReadsByIndex(ts, checker, declaration) || (initializer !== undefined
      && (ts.isElementAccessExpression(initializer)
        || readsIndexSignature(ts, checker, initializer))),
  };
}

function expressionLines(ts, checker, sourceFile, span) {
  const [lo, hi] = span;
  const anyLines = new Set();
  const assertionLines = new Set();
  const subscriptLines = new Set();
  const definiteLines = new Set();
  function addLines(lines, start, end) {
    for (let l = Math.max(lo, lineOf(sourceFile, start)); l <= Math.min(hi, lineOf(sourceFile, end)); l++) {
      lines.add(l);
    }
  }
  function addNode(lines, node) {
    addLines(lines, node.getStart(sourceFile), node.getEnd());
  }
  function visit(node) {
    if (bindingReadsByIndex(ts, checker, node)) addNode(subscriptLines, node);
    if (isAssignmentPattern(ts, node)) {
      for (const target of assignmentTargetsByIndex(ts, checker, node)) addNode(subscriptLines, target);
    }
    if (ts.isIdentifier(node)) {
      const line = lineOf(sourceFile, node.getStart(sourceFile));
      if (line >= lo && line <= hi) {
        const facts = declarationFacts(ts, checker, declarationOf(ts, checker, node));
        if (facts.asserted) assertionLines.add(line);
        if (facts.definite) definiteLines.add(line);
        if (facts.indexed) subscriptLines.add(line);
      }
    }
    // Every expression, not a list of kinds: `f(u as any)` holds no access or call
    // whose own type is any, only the cast.
    if (ts.isExpression(node) && !namesNoValue(ts, node)) {
      const start = node.getStart(sourceFile);
      const line = lineOf(sourceFile, start);
      if (line >= lo && line <= hi
        && (checker.getTypeAtLocation(node).flags & ts.TypeFlags.Any)) anyLines.add(line);
      if (isAssertion(ts, node)) addLines(assertionLines, start, node.getEnd());
      if (ts.isElementAccessExpression(node) || readsIndexSignature(ts, checker, node)) {
        addLines(subscriptLines, start, accessChainEnd(ts, node));
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return { anyLines, assertionLines, subscriptLines, definiteLines };
}

function isJSDoc(ts, node) {
  return node.kind >= ts.SyntaxKind.FirstJSDocNode && node.kind <= ts.SyntaxKind.LastJSDocNode;
}

// The line a directive ending on `line` suppresses: tsc walks up from a diagnostic
// over blank lines and `//` comment lines until it meets a directive
// (markPrecedingCommentDirectiveLine), so this walks down the same way. Null when
// only blank and comment lines follow.
function directiveTarget(sourceFile, line) {
  const starts = sourceFile.getLineStarts();
  const text = sourceFile.text;
  for (let i = line; i < starts.length; i++) {
    const lineText = text.slice(starts[i], i + 1 < starts.length ? starts[i + 1] : text.length).trim();
    if (lineText !== "" && !/^\s*\/\/.*$/.test(lineText)) return i + 1;
  }
  return null;
}

// Every comment is trivia of a token the parser produced: leading trivia when it
// starts after a line break, trailing trivia when it shares the line with the token
// before it (`x = 1; // @ts-ignore`). Collecting both for every token finds every
// comment and nothing inside a literal. A bare scanner over the text cannot do this:
// it does not know that `/`...`/` is a regular expression, and loses track after a
// backtick inside one.
function commentDirectives(ts, sourceFile) {
  const text = sourceFile.text;
  const seen = new Set();
  const lines = new Set();
  const targets = new Set();
  let nocheck = false;
  function token(node) {
    const ranges = (ts.getLeadingCommentRanges(text, node.pos) || [])
      .concat(ts.getTrailingCommentRanges(text, node.end) || []);
    for (const range of ranges) {
      if (seen.has(range.pos)) continue;
      seen.add(range.pos);
      const comment = text.slice(range.pos, range.end);
      if (/@ts-(ignore|expect-error)/.test(comment)) {
        const last = lineOf(sourceFile, range.end);
        for (let l = lineOf(sourceFile, range.pos); l <= last; l++) lines.add(l);
        const target = directiveTarget(sourceFile, last);
        if (target !== null) targets.add(target);
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
  const sorted = (set) => [...set].sort((a, b) => a - b);
  return { lines: sorted(lines), targets: sorted(targets), nocheck };
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
  const options = effectiveOptions(ts, request, repo, basePath, target);
  const rootNames = (repo ? repo.fileNames : []).concat([target]);

  const host = ts.createCompilerHost(options);
  const program = ts.createProgram({ rootNames, options, host });
  const sourceFile = program.getSourceFile(target);
  if (!sourceFile) refuse(target + " is not in the program");
  // skipLibCheck is forced, so a declaration file's own errors are never reported.
  if (sourceFile.isDeclarationFile) refuse(target + " is a declaration file");

  // Diagnostics first: a type query on a name the checker never resolved during the
  // check (a JSX tag, for one) adds a "Cannot find name" of its own, which would read
  // as a defect in the audited code.
  const diagnostics = ts.getPreEmitDiagnostics(program).map((d) => serializeDiagnostic(ts, d));
  const checker = program.getTypeChecker();
  const lines = expressionLines(ts, checker, sourceFile, request.span);
  const sorted = (set) => [...set].sort((a, b) => a - b);
  const response = {
    protocol: PROTOCOL,
    version: ts.version,
    files: program.getSourceFiles().map((f) => path.resolve(f.fileName)),
    diagnostics,
    any_lines: sorted(lines.anyLines),
    assertion_lines: sorted(lines.assertionLines),
    directives: commentDirectives(ts, sourceFile),
    subscript_lines: sorted(lines.subscriptLines),
    definite_assignment_lines: sorted(lines.definiteLines),
  };
  process.stdout.write(JSON.stringify(response) + "\n");
}

try {
  main();
} catch (err) {
  refuse("internal error: " + (err && err.stack ? err.stack : err));
}
