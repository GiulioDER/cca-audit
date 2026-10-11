# Running semgrep on a remote host

Some machines cannot run semgrep. Windows Smart App Control, for one, refuses its binary however
it is installed, and every `taint` claim then escalates to UNCERTAIN. If you have a Linux host you
can reach over ssh, cca-audit can run semgrep there instead: set `CCA_REMOTE_SEMGREP` and the
checker sends the rules file and the one file under audit to that host whenever local semgrep
cannot run. When local semgrep works, nothing leaves your machine.

Only semgrep goes remote. Nothing that executes the audited code (the repro and property runners,
clippy builds) ever does. On the host, semgrep runs as a dedicated unprivileged account that can
start exactly one program, `cca-analyzer-runner`, whatever the client asks; the runner refuses
anything but a version check or one rules file plus one `.py` or `.rs` file under 5 MB, scans it
in a private temporary folder under a memory and CPU cap, and deletes the folder.

Design: [`superpowers/specs/2026-10-11-remote-semgrep-design.md`](superpowers/specs/2026-10-11-remote-semgrep-design.md).

## 1. A key for this purpose only (workstation)

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/cca_analyzer" -C cca-workstation
```

`$HOME`, not `~`: PowerShell passes `~` to native programs unexpanded, so `ssh-keygen` looks
for a folder literally named `~`. Leave the passphrase empty or load the key into an agent: the checker runs ssh with
`BatchMode=yes`, so a passphrase prompt is a failure, not a wait.

## 2. The host account (once, with sudo)

Copy the runner and the public key to the host first, from the cca-audit version you run locally:

```bash
scp cca_checks/remote/runner.py "$HOME/.ssh/cca_analyzer.pub" <admin>@<host>:
```

Then, on the host:

```bash
sudo adduser --system --group --home /home/cca-analyzer --shell /bin/sh cca-analyzer
sudo loginctl enable-linger cca-analyzer
sudo python3 -m venv /home/cca-analyzer/venv
sudo /home/cca-analyzer/venv/bin/pip install 'semgrep==1.178.0'
sudo install -d -o root -g root -m 755 /home/cca-analyzer/bin /home/cca-analyzer/.ssh
sudo install -o root -g root -m 755 runner.py /home/cca-analyzer/bin/cca-analyzer-runner
echo "restrict,command=\"/home/cca-analyzer/bin/cca-analyzer-runner\" $(cat cca_analyzer.pub)" \
  | sudo tee /home/cca-analyzer/.ssh/authorized_keys >/dev/null
sudo chmod 644 /home/cca-analyzer/.ssh/authorized_keys
sudo install -d -o cca-analyzer -g cca-analyzer -m 700 /home/cca-analyzer/locks
sudo chown root:root /home/cca-analyzer && sudo chmod 755 /home/cca-analyzer
```

* `--system` gives the account no password and no membership of any other group, in particular
  not one that can read the host's secrets.
* Everything the account runs or is trusted by (its home, `bin/`, the runner, the semgrep
  virtualenv, `.ssh/` and `authorized_keys`) is owned by root. The account can write only its lock
  folder and `/tmp`. So a bug in semgrep or a parser, exploited by a crafted file, cannot add a key,
  replace the runner or plant a module for later runs: it stays a one-shot. sshd accepts a
  root-owned `authorized_keys`.
* The runner's first line is `#!/usr/bin/python3 -I`, so the host needs `/usr/bin/python3`, and
  files beside the runner are never imported.
* `enable-linger` gives the account a systemd user manager, which is how the runner caps each
  semgrep run's memory and CPU without root.
* `restrict` turns off port, agent and X11 forwarding and the terminal; `command=` makes the runner
  the only program the key can start.

Then cap the whole account, so nothing it does (open connections, the runner itself, a version
probe) can take more than this, whatever the per-run limits:

```bash
sudo mkdir -p /etc/systemd/system/user-$(id -u cca-analyzer).slice.d
printf '[Slice]\nMemoryMax=3G\nTasksMax=64\nCPUQuota=150%%\n' \
  | sudo tee /etc/systemd/system/user-$(id -u cca-analyzer).slice.d/limits.conf >/dev/null
sudo systemctl daemon-reload
```

## 3. The workstation side

In `~/.ssh/config`:

```
Host cca-analyzer
    HostName <host>
    User cca-analyzer
    IdentityFile ~/.ssh/cca_analyzer
    IdentitiesOnly yes
```

Record the host key once, by hand (the checker never will). First, on the host, print its key's
fingerprint:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Then connect once from the workstation, and type `yes` only if the fingerprint ssh shows is that
one:

```bash
ssh -o BatchMode=no cca-analyzer < /dev/null
```

It prints the runner's refusal of an empty request, which is the expected answer. Then set the
variable in your shell profile:

```bash
export CCA_REMOTE_SEMGREP=cca-analyzer
```

The value must be a plain ssh name: an alias or `user@host`, neither part starting with `-`.
Anything else is reported as invalid and ignored.

**What you are trusting.** A taint claim is refuted when semgrep finds no sink, so with this
setting the remote host joins the trust base for refutations: the audited repository still cannot
influence the scan, but a compromised host, or anyone holding its host key, could answer "no sink"
for a file that has one. That is why the host key is checked out of band above, and why every
remotely settled verdict names the host in its evidence.

## 4. Checking it

```bash
python -m cca_checks capabilities --file any_file.py
```

When the remote serves, `taint` is not in `unavailable` and the output carries
`"remote": {"taint": "semgrep 1.178.0 on cca-analyzer"}`. When it cannot, `taint` stays
unavailable and the reason names both sides, for example
`local: ... WinError 4551 ...; remote cca-analyzer: no runner answer (...)`.

Verdicts settled remotely keep `source: semgrep`, and their evidence ends with
`(semgrep <version> on <destination>)`.

## 5. Checking the isolation (rerun after any host change)

From the workstation, the key must not open a shell:

```bash
ssh -n cca-analyzer id
```

Expected: the runner's JSON refusal (`not a well-formed tar archive`), never a uid line. (`-n`
gives ssh an empty stdin; without it the runner waits for a request on your terminal.)

On the host, the account must not read anything of the services that run there. Name their real
secret locations, not only `/home`:

```bash
sudo -u cca-analyzer sh -c 'ls /home/*/ /opt /srv 2>&1; cat /home/*/.env /etc/<service>/*.env 2>&1' | head -20
```

Expected: `Permission denied` (or no such file) for every secret. And the account must not write
anything it runs:

```bash
sudo -u cca-analyzer sh -c 'touch ~/bin/x ~/.ssh/x ~/venv/x 2>&1'
```

Expected: `Permission denied` three times.

The per-run caps must really apply. This starts a scope as the account and prints its memory and
CPU limits:

```bash
sudo -u cca-analyzer XDG_RUNTIME_DIR=/run/user/$(id -u cca-analyzer) \
  systemd-run --user --scope --quiet -p MemoryMax=2G -p CPUQuota=100% \
  sh -c 'd=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); cat $d/memory.max $d/cpu.max'
```

Expected: `2147483648` and `100000 100000`. If it prints `max`, the memory or CPU controller is not
delegated to user managers on this host (cgroup v1, or no delegation), so the caps are silently
not applied: rely on the account slice above, or fix delegation, before enabling the remote.

If anything here fails, remove `CCA_REMOTE_SEMGREP` until the host is fixed.
