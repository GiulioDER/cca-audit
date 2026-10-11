# Remote semgrep: settling taint claims where semgrep cannot run locally

Status: design approved section by section on 2026-10-11; this document awaits review.

## 1. Goal

Some workstations cannot execute semgrep. On the author's, Windows Smart App Control refuses the
binary however it is installed (`WinError 4551`), so every `taint` claim escalates to UNCERTAIN
and `python -m cca_checks capabilities` reports taint unavailable. Turning Smart App Control off is
a one-way door and is not an option.

This design lets an operator name one remote host, reached over ssh, where semgrep runs instead.
Success means: with the setting in place, taint claims settle by semgrep as they do in CI; with
it absent, nothing changes; and every report says honestly where the scan ran or why it could not.

## 2. Constraints

1. **Only static analysis goes remote, and only semgrep.** The repro and property runners execute
   the audited code and never leave the machine. Clippy builds the crate, which runs its
   `build.rs` and proc macros, so it is execution too and stays local. Pyright runs locally on the
   author's workstation and is out of scope (nothing needs it remote).
2. **What is shipped is untrusted.** Hunt mode audits code nobody here wrote, and the remote host
   may run other services (the author's runs live production services). The remote side must not
   let a crafted file reach anything beyond one bounded semgrep run.
3. **Availability is decided by execution, and reported honestly.** `toolpath` and `capabilities`
   probe by running a tool, never by finding a file; a remote tool is held to the same rule.
4. **The audited repository controls nothing about the remote.** It cannot set the host, cannot
   name the command, and cannot put a byte into the remote command line.

## 3. Approach

A **forced-command runner**. The remote account's key may run exactly one program, the runner,
whatever the client asks. The client sends a tar archive on stdin holding the rules file and the
one file under audit, under fixed names; the runner unpacks it into a private temporary folder,
runs semgrep with fixed flags under resource limits, returns semgrep's JSON, and deletes the
folder. Rejected: running `ssh host semgrep <args>` (the key could run anything, and the remote
shell would parse arguments built partly from audited names), and an HTTP service (a standing
listener and its own authentication, for a handful of calls per audit).

**Local first.** The remote is used only when local semgrep fails its execution probe and a remote
is configured. Code leaves the machine only when it must.

## 4. Remote host setup (one time, by the operator, with sudo)

Run as an administrator on the host. `<pubkey>` is the public half of a key made for this purpose
only on the workstation (`ssh-keygen -t ed25519 -f ~/.ssh/cca_analyzer -C cca-workstation`).

```bash
sudo adduser --system --group --home /home/cca-analyzer --shell /bin/sh cca-analyzer
sudo loginctl enable-linger cca-analyzer
sudo -u cca-analyzer python3 -m venv /home/cca-analyzer/venv
sudo -u cca-analyzer /home/cca-analyzer/venv/bin/pip install 'semgrep==<pinned version>'
sudo install -d -o cca-analyzer -g cca-analyzer -m 700 /home/cca-analyzer/bin /home/cca-analyzer/.ssh
sudo install -o cca-analyzer -g cca-analyzer -m 755 runner.py /home/cca-analyzer/bin/cca-analyzer-runner
echo 'restrict,command="/home/cca-analyzer/bin/cca-analyzer-runner" <pubkey>' \
  | sudo -u cca-analyzer tee /home/cca-analyzer/.ssh/authorized_keys >/dev/null
sudo chmod 600 /home/cca-analyzer/.ssh/authorized_keys
sudo chmod 750 /home/cca-analyzer
```

* `--system` gives no login password and no membership of any other group: in particular not the
  group that can read the host's secrets.
* `enable-linger` starts a systemd user manager for the account, so the runner can put semgrep in a
  cgroup with `systemd-run --user --scope` without root.
* `restrict` disables port, agent and X11 forwarding and the pty; `command=` makes the runner the
  only program the key can start.
* `runner.py` is `cca_checks/remote/runner.py` from the same cca-audit version as the workstation.
  The pinned semgrep version is the one CI uses, written into the plan when it is built.

Workstation side, in `~/.ssh/config`:

```
Host cca-analyzer
    HostName <host>
    User cca-analyzer
    IdentityFile ~/.ssh/cca_analyzer
    IdentitiesOnly yes
```

and the host key in `known_hosts` (connect once by hand to accept it; the tool never will).

## 5. The runner (`cca_checks/remote/runner.py`)

Python standard library only, so it needs no cca-audit install on the host.

**Protocol.** stdin is a tar stream. Its first member is `request.json`:
`{"protocol": 1, "op": "version" | "semgrep"}`. For `semgrep` the archive also holds exactly two
regular files, `rules.yaml` and `target.<ext>` with `<ext>` in `{py, rs}`. stdout is one JSON
document; the exit code is 0 for any answered request and non-zero only when the runner itself
crashed.

* `version`: runs `semgrep --version` and returns `{"ok": true, "semgrep": "<version>"}`, or
  `{"ok": false, "error": "<reason>"}`.
* `semgrep`: returns `{"ok": true, "result": <semgrep's JSON, unchanged>}`, or
  `{"ok": false, "error": "<reason>"}`.

**Refusals, each before anything runs:** an unknown protocol or operation; any member that is not
a regular file (symlink, hard link, device, folder); any name that is absolute, contains `..`, or
is not one of the expected names; a missing or extra member; a target extension outside the
allowlist; a total size over 5 MB; more than two runs already in progress.

**The run.** Unpack into `tempfile.mkdtemp()` (mode 0700), `cd` there, and run

```
systemd-run --user --scope --quiet --collect -p MemoryMax=2G -p MemorySwapMax=0 -p CPUQuota=100% \
  nice -n 15 timeout 120 /home/cca-analyzer/venv/bin/semgrep --config rules.yaml --json \
  --metrics=off --disable-version-check --quiet --disable-nosem --no-git-ignore target.<ext>
```

with a minimal environment (`PATH`, `HOME` set to the temp folder, `XDG_RUNTIME_DIR`). The temp
folder is removed in a `finally`, on success, error and timeout alike. Concurrency is bounded by
two lock files taken with `fcntl.flock` in non-blocking mode; a third request is refused, not
queued.

The flags are the ones `run_semgrep` uses locally, for the same reason: an empty result licenses a
refutation, so the audited file's own suppression comments and ignore files must not be honoured.

## 6. The client

**Setting.** `CCA_REMOTE_SEMGREP`, read from the environment only, like every other `CCA_`
setting in `cca_checks/config.py`. Its value is an ssh destination: an alias or `user@host`,
matching `^[A-Za-z0-9._-]+(@[A-Za-z0-9._-]+)?$`. Anything else, notably a value beginning with
`-`, is treated as not configured and reported as invalid.

**Transport (`cca_checks/remote/__init__.py`).** Two functions and no general one:

* `remote_semgrep_version(dest) -> tuple[str | None, str | None]`: (version, reason).
* `remote_semgrep(dest, rules: bytes, target: bytes, ext: str) -> dict | None`: semgrep's JSON, or
  `None` with the reason recorded for the verdict's evidence.

`ssh` is resolved by `toolpath.resolve_tool` and run as
`ssh -T -o BatchMode=yes -o ConnectTimeout=10 <dest>` with the archive on stdin and the
`CCA_TIMEOUT_S` timeout plus a margin. `BatchMode` makes an unknown host key a failure rather than
a prompt or a silent acceptance. A target over 5 MB or with another extension is refused before
anything is sent.

**`run_semgrep`.** If `toolpath.tool_unavailable_reason("semgrep")` is `None`, it runs locally,
unchanged. Otherwise, if a remote is configured, it reads the rules file and the target and calls
`remote_semgrep`. The returned JSON goes through the same validation as a local run (`errors`
empty, `paths.scanned` non-empty, `results` a list). Every failure, of ssh, the timeout, the
runner's refusal or the output's shape, returns `None`: the existing "could not tell, escalate"
path. Downstream code reads only rule IDs and line numbers from results (`hits_in_span`,
`_control_fired`); `_describe` prints the local file name it is given, so the remote temp path
never reaches a verdict.

## 7. Reporting

* `capabilities`: when local semgrep fails and a remote is configured, the backend's
  `unavailable_claim_types` probes the runner's `version` operation (cached per process). If it
  answers, `taint` is not unavailable and the output gains
  `"remote": {"taint": "semgrep <version> on <dest>"}`. If it does not, `taint` stays unavailable
  with both reasons: `local: <reason>; remote <dest>: <reason>`. Not configured: unchanged.
* A remotely settled taint verdict keeps `source: semgrep` (same tool, same rules, same binding
  status) and its evidence names the host: `(semgrep <version> on <dest>)`.
* A remote failure is UNCERTAIN with its reason, never FALSE_POSITIVE.

## 8. Testing

Every behaviour test is shown red under a named mutation before it is trusted green.

| Area | Tests |
|---|---|
| runner refusals | symlink member, hard link, `..`, absolute name, extra member, missing member, wrong extension, over 5 MB, unknown op, unknown protocol: JSON error, and a fake semgrep that writes a marker file proves it never ran |
| runner run | exact flag list including `--disable-nosem` and `--no-git-ignore`; temp folder removed after success, crash and timeout; a third concurrent run refused |
| destination | `-oProxyCommand=x`, a space, an empty value: not configured, reported invalid |
| transport | round trip with ssh replaced by a direct call to the runner and the fake semgrep; every failure maps to `None`; size and extension refused before sending |
| local first | when local semgrep works, the transport is never called |
| capabilities | remote ok: available plus `remote`; remote failing: both reasons; unset: output identical to today |

CI sets no remote, so Ubuntu keeps testing local semgrep exactly as now.

**Live checks after the operator's setup:**

1. Isolation: as `cca-analyzer`, reading the other account's home and secrets fails with
   permission denied; `ssh cca-analyzer id` returns the runner's refusal, not a user id.
2. A pre-registered measurement on the author's workstation with `CCA_REMOTE_SEMGREP` set: how many
   of the 11 semgrep-dependent tests that fail there now pass (some call the semgrep binary
   directly rather than `run_semgrep`, so not all are expected to), and whether `capabilities`
   reports the remote.

## 9. Out of scope

* Pyright, clippy, or any other tool remote.
* More than one remote host, failover, or queueing beyond the two-run limit.
* Network isolation of the semgrep process beyond its offline flags (systemd's `IPAddressDeny`
  needs privileges a user scope may not have; a later hardening if wanted).
* Installing or updating the runner on the host automatically.

## 10. Corrections from the whole-branch review (2026-10-11)

Found by a security review of the implementation; the sections above stay as approved, and where
they disagree with this one, this one wins. `docs/remote-semgrep.md` carries the operator steps.

1. **§5, refusals:** a member must be a plain file (`REGTYPE`/`AREGTYPE`, not sparse), and the 5 MB
   budget is checked from each header before its data is read. A sparse member passed `isreg()`
   and was read with its holes filled, so a 2.5 KB archive could allocate gigabytes in the runner,
   outside every cap. Malformed input and unexpected errors are answered in JSON. A request that
   does not arrive within 30 s is dropped.
2. **§5, the run:** `timeout -k 5 120` sits inside the scope, as §5 specified and the plan dropped,
   so semgrep-core dies with its run. The version probe takes a slot and runs through the same
   scope, so a host where the scope cannot start reports semgrep unavailable. `XDG_RUNTIME_DIR`
   defaults to `/run/user/<uid>`.
3. **§4, host setup:** the account's home, `bin/`, runner, virtualenv, `.ssh/` and
   `authorized_keys` are root-owned; the account writes only its lock folder and `/tmp`. The
   runner's shebang is `#!/usr/bin/python3 -I`. The account's user slice is capped
   (`MemoryMax=3G`, `TasksMax=64`, `CPUQuota=150%`). The isolation checks add a write test and a
   check that the cgroup limits really apply.
4. **§6, the setting:** neither part of the destination may start with `-`, and `--` ends ssh's
   options before it. A remote answer must list exactly `target.<ext>` as scanned.
5. **§7, trust:** a remote scan puts that host in the trust base for refutations. The operator
   verifies the host key fingerprint out of band; the verdict names the host.
