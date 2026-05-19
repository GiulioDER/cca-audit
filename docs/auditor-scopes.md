# Auditor Scopes

The key design principle of CCA-Audit is **non-overlapping scopes**. Each auditor is the sole authority for its domain. This prevents duplicate findings and conflicting recommendations.

## Scope Matrix

| Check | Code | Bug | Security | Perf | Doc | Env | Dep |
|-------|:----:|:---:|:--------:|:----:|:---:|:---:|:---:|
| Type safety / `any` usage | X | | | | | | |
| Complexity / nesting | X | | | | | | |
| DRY violations | X | | | | | | |
| Magic numbers | X | | | | | | |
| Naming conventions | X | | | | | | |
| Dead code / unused imports | X | | | | | | |
| TODO/FIXME accumulation | X | | | | | | |
| Debug statements | X | | | | | | |
| Null/None references | | X | | | | | |
| Error handling gaps | | X | | | | | |
| Race conditions | | X | | | | | |
| Resource leaks | | X | | | | | |
| Async/concurrency bugs | | X | | | | | |
| SQL injection | | | X | | | | |
| Command injection | | | X | | | | |
| XSS | | | X | | | | |
| Auth/session issues | | | X | | | | |
| Secrets exposure | | | X | | | | |
| CSRF | | | X | | | | |
| Dependency CVEs | | | X | | | | |
| N+1 queries | | | | X | | | |
| Hot-path overhead | | | | X | | | |
| Memory issues | | | | X | | | |
| Connection management | | | | X | | | |
| Redundant computation | | | | X | | | |
| I/O bottlenecks | | | | X | | | |
| Missing docstrings | | | | | X | | |
| Stale comments | | | | | X | | |
| Missing type annotations | | | | | X | | |
| API doc gaps | | | | | X | | |
| Config completeness | | | | | | X | |
| Value format validation | | | | | | X | |
| Naming consistency | | | | | | X | |
| Outdated packages | | | | | | | X |
| Unmaintained packages | | | | | | | X |
| License compliance | | | | | | | X |
| Unused dependencies | | | | | | | X |

## Boundary Rules

### Security is the single authority
The security auditor owns ALL security checks. Other auditors must NOT flag:
- Injection vulnerabilities (even if they look like "bugs")
- Hardcoded secrets (even if they look like "code quality")
- Auth issues (even if they look like "missing middleware")
- Dependency CVEs (even though dep-auditor handles dependency health)

### Bug vs Security
- **Bug auditor**: "This error is swallowed" (correctness issue)
- **Security auditor**: "This error leaks stack traces to users" (information disclosure)
- **Bug auditor**: "This file handle isn't closed" (resource leak)
- **Security auditor**: "This user input isn't sanitized" (injection)

### Code vs Doc
- **Code auditor**: "This function has 15 levels of nesting" (complexity)
- **Doc auditor**: "This non-obvious function has no docstring" (documentation gap)
- **Code auditor**: "This TODO has been here for 6 months" (code hygiene)
- **Doc auditor**: "This docstring says it returns a string but it returns int" (stale doc)

### Perf vs Bug
- **Perf auditor**: "This cache grows without bound" (memory performance)
- **Bug auditor**: "This cache entry is never invalidated, causing stale reads" (correctness)

### Dep vs Security
- **Dep auditor**: "This package hasn't been updated in 3 years" (maintenance risk)
- **Security auditor**: "This package has CVE-2024-XXXX" (known vulnerability)

## Adding a New Auditor

When adding a custom auditor, you MUST:
1. Define its exclusive scope
2. List what it does NOT check (with pointers to which auditor does)
3. Update this matrix
4. Ensure no overlap with existing auditors

See [extending.md](extending.md) for the full guide.
