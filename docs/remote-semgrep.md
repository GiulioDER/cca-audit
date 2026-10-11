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
ssh-keygen -t ed25519 -f ~/.ssh/cca_analyzer -C cca-workstation
```

Leave the passphrase empty or load the key into an agent: the checker runs ssh with
`BatchMode=yes`, so a passphrase prompt is a failure, not a wait.

## 2. The host account (once, with sudo)

Copy the runner and the public key to the host first, from the cca-audit version you run locally:

```bash
scp cca_checks/remote/runner.py ~/.ssh/cca_analyzer.pub <admin>@<host>:
```

Then, on the host:

```bash
sudo adduser --system --group --home /home/cca-analyzer --shell /bin/sh cca-analyzer
sudo loginctl enable-linger cca-analyzer
sudo -u cca-analyzer python3 -m venv /home/cca-analyzer/venv
sudo -u cca-analyzer /home/cca-analyzer/venv/bin/pip install 'semgrep==1.178.0'
sudo install -d -o cca-analyzer -g cca-analyzer -m 700 /home/cca-analyzer/bin /home/cca-analyzer/.ssh
sudo install -o cca-analyzer -g cca-analyzer -m 755 runner.py /home/cca-analyzer/bin/cca-analyzer-runner
echo "restrict,command=\"/home/cca-analyzer/bin/cca-analyzer-runner\" $(cat cca_analyzer.pub)" \
  | sudo -u cca-analyzer tee /home/cca-analyzer/.ssh/authorized_keys >/dev/null
sudo chmod 600 /home/cca-analyzer/.ssh/authorized_keys
sudo chmod 750 /home/cca-analyzer
```

* `--system` gives the account no password and no membership of any other group, in particular
  not one that can read the host's secrets.
* `enable-linger` gives it a systemd user manager, which is how the runner caps semgrep's memory
  and CPU without root.
* `restrict` turns off port, agent and X11 forwarding and the terminal; `command=` makes the runner
  the only program the key can start.

## 3. The workstation side

In `~/.ssh/config`:

```
Host cca-analyzer
    HostName <host>
    User cca-analyzer
    IdentityFile ~/.ssh/cca_analyzer
    IdentitiesOnly yes
```

Accept the host key once, by hand (the checker never will):

```bash
ssh -o BatchMode=no cca-analyzer < /dev/null
```

It prints the runner's refusal of an empty request, which is the expected answer. Then set the
variable in your shell profile:

```bash
export CCA_REMOTE_SEMGREP=cca-analyzer
```

The value must be a plain ssh name: an alias or `user@host`. Anything else, notably a value
starting with `-`, is reported as invalid and ignored.

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
ssh cca-analyzer id
```

Expected: the runner's JSON refusal (`not a tar archive`), never a uid line.

On the host, the account must not read anyone else's files:

```bash
sudo -u cca-analyzer sh -c 'ls /home/*/ 2>&1; cat /home/*/.env 2>&1' | head
```

Expected: `Permission denied` for every other account. If anything readable comes back, remove
`CCA_REMOTE_SEMGREP` until the host is fixed.
