# Security Policy

## The threat model, stated plainly

`cca_checks` — the deterministic verification layer behind the `fp-check` gate — **executes
code from the repository under audit.** This is not an edge case; it is the mechanism the
package exists to provide. Concretely:

- **`repro_runner.py` and `property_check.py` both invoke `pytest` as a subprocess**
  (`python -m pytest`) against paths inside the audited repo. `pytest` imports `conftest.py`
  at collection time, before any test selection happens. **Pointing this tool at an untrusted
  repository executes that repository's `conftest.py` — and anything it imports — with your
  privileges and in your environment.** There is no sandboxing at this layer; the subprocess
  inherits the calling user's filesystem access, environment variables, and network reachability.
- **`substrate.py` runs the audited target twice** — once at float64, once against a 50-digit
  `mpmath` reference — and **monkeypatches the target module's `math`/`cmath` bindings for the
  duration of each call.** A target with side effects fires them on both runs. This module is
  explicitly documented as not thread-safe for the same reason.
- **Hunt mode (`/audit-fix hunt <paths>`) is designed to be pointed at code you did not write** —
  an OSS dependency, a repo you're evaluating, a legacy service. That is exactly the untrusted
  case above, by design, not by accident.
- `pyright_check.py` and `semgrep_check.py` shell out to external binaries resolved against the
  audited repo's working directory and can read the audited repo's own tool config
  (`pyrightconfig.json`, `.semgrepignore`, `# nosemgrep`). The DEEP self-audit (2026-07-22,
  fixed in the commits merged via PRs #18–#20) hardened several of these — explicit `PATH`
  resolution instead of bare-name lookup, a generated pyright config that ignores the audited
  repo's own `typeCheckingMode`/`# type: ignore` overrides, `--disable-nosem`/`--no-git-ignore`
  for semgrep — because the audited repo's own configuration is adversarial input, not trusted
  context.

**The documented mitigation is a sandbox**: run the pipeline against untrusted or third-party
code inside a container, under seccomp, or in a scrubbed, offline, disposable environment —
never directly on a machine holding credentials, SSH keys, or access to systems you care about.
This is stated in the module docstrings of `repro_runner.py`, `property_check.py`, and
`substrate.py` themselves; this file exists so it is stated somewhere a security reviewer looks
first, too.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.4.x   | Yes — current release line |
| < 0.4   | No — upgrade; the DEEP self-audit (PRs #18–#20, #22) fixed real security findings (see `CHANGELOG.md`) that are not backported |

## Reporting a vulnerability

Use **[GitHub Security Advisories](https://github.com/GiulioDER/cca-audit/security/advisories/new)**
on this repository to report privately. If you'd rather not use Advisories, open a regular
GitHub issue and state that it's security-sensitive — this project does not yet have a
dedicated inbox separate from GitHub, and I'd rather point you at the real channel than
invent one.

**Response time: best effort.** This is a solo-maintained project, not a team with an on-call
rotation. I will not promise an SLA I can't honor — I'll acknowledge and look at reports as
soon as I reasonably can, but there is no guaranteed turnaround.

## Out of scope

**The tool executing the audited repository's code is documented, intended behavior — not a
vulnerability.** Please don't file "the auditor ran my `conftest.py`" or "the substrate checker
imported my module and mutated its globals" as a security report; both are covered above and
are the mechanism, not a bug in it. If you find a way the tool executes code it should
*refuse* to run (e.g., a bypass of the target-viability pre-flight, or execution triggered
without any of the documented `pytest`/subprocess paths above), that *is* in scope — please
report it.

Also out of scope: vulnerabilities in third-party tools this project shells out to
(`pyright`, `semgrep`, `pytest`, `hypothesis`, `mpmath`) — report those upstream. This project's
own use of them (config injection, argv[0] resolution, etc.) is in scope.
