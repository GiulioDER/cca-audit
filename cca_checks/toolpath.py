"""Resolve external analyzer binaries to a trusted absolute path.

Never pass a bare name as argv[0] to subprocess when the working directory is the
repo under audit. On Windows, CreateProcess resolves a bare argv[0] against the
CURRENT DIRECTORY *before* PATH, and `shutil.which` mirrors that rule -- so a repo
that ships `pyright.exe` / `semgrep.exe` in its root would be executed with the
auditor's privileges and environment merely by pointing the tool at it. `hunt` mode
exists precisely to be pointed at code nobody here wrote, which makes this the
tool's most exposed surface.

Resolution therefore does two things: it returns an absolute path (so the launch is
unambiguous), and it REFUSES a binary that resolves inside the audited tree. A
refusal returns None, which every caller already maps onto its existing "tool
unavailable -> UNCERTAIN" escalation. Failing closed here costs a confirmation; the
alternative costs the auditor's machine.
"""

import os
import shutil


def _is_inside(path: str, root: str) -> bool:
    """True if `path` lies within `root`. False when they are not comparable."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        # Different drives on Windows, or a mix of absolute and relative paths:
        # not comparable, therefore not inside.
        return False


def resolve_tool(name: str, cwd: str | None = None) -> str | None:
    """Absolute path to analyzer `name`, or None if it is missing or untrusted.

    None means "tool unavailable" to every caller, which is the existing escalate
    path -- so a hijack attempt degrades the run to LLM adjudication rather than
    executing the audited repo's binary.

    The refusal is deliberately narrow: only a binary sitting DIRECTLY in the
    working directory is rejected. Refusing anything anywhere under the audited
    tree looks safer but is not -- a project-local `.venv/Scripts/pyright.exe` is
    the standard Python layout, and rejecting it would silently switch the entire
    deterministic layer off for the most common setup, degrading every claim to
    UNCERTAIN with no visible reason. That trades a rare attack for a routine
    self-disable.

    The cwd-first hazard is closed at the source instead: passing `path=` to
    `shutil.which` suppresses the implicit current-directory entry that Windows
    (and CreateProcess) would otherwise search before PATH.
    """
    # Explicit PATH: without it, shutil.which mirrors CreateProcess and searches
    # the current directory first on Windows.
    found = shutil.which(name, path=os.environ.get("PATH"))
    if not found:
        return None
    resolved = os.path.realpath(found)
    root = os.path.realpath(cwd if cwd is not None else os.getcwd())
    if os.path.dirname(resolved) == root:
        # Planted in the root of the repo under audit. A real toolchain is never
        # installed there, so this is the hijack case.
        return None
    return resolved
