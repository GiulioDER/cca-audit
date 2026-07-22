# CCA-Audit installer for Claude Code (Windows)
# Copies agent and command files to .claude/ in the current project.
#
# Works in two modes:
#   - Local:      run from a cloned repo (.\claude-code\install.ps1)
#   - Standalone: piped from the web
#                 (irm https://raw.githubusercontent.com/GiulioDER/cca-audit/master/claude-code/install.ps1 | iex)
#                 it shallow-clones the repo to a temp dir first.

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/GiulioDER/cca-audit.git"
$RepoRef = "master"

# Resolve the directory this script lives in (empty when piped via iex).
$ScriptDir = $null
if ($PSCommandPath) {
    $ScriptDir = Split-Path -Parent $PSCommandPath
} elseif ($MyInvocation.MyCommand.Path) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$CleanupDir = $null
try {
    # Decide where to copy the files from.
    if ($ScriptDir -and (Test-Path (Join-Path $ScriptDir "agents"))) {
        # Local mode: run from a checkout.
        $SrcDir = $ScriptDir
    } else {
        # Standalone mode: fetch the repo into a temp dir.
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw "git is required for the piped install."
        }
        $CleanupDir = Join-Path ([System.IO.Path]::GetTempPath()) ("cca-audit-" + [System.Guid]::NewGuid().ToString("N"))
        Write-Host "Fetching CCA-Audit ($RepoRef)..."
        # Redirect native stderr to $null rather than merging it with 2>&1.
        # git writes "Cloning into '...'" to stderr, and under Windows PowerShell
        # 5.1 a MERGED native stderr line becomes an ErrorRecord -- which, with
        # $ErrorActionPreference = "Stop", aborts the advertised `irm ... | iex`
        # one-liner on a perfectly successful clone. Check $LASTEXITCODE instead,
        # so a genuine clone failure is caught here rather than surfacing later as
        # the misleading "no agent files found".
        git clone --depth 1 --branch $RepoRef $RepoUrl (Join-Path $CleanupDir "repo") 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "could not clone $RepoUrl (network/proxy/auth?)."
        }
        $SrcDir = Join-Path (Join-Path $CleanupDir "repo") "claude-code"
    }

    $AgentsDir = ".claude\agents"
    $CommandsDir = ".claude\commands"
    New-Item -ItemType Directory -Force -Path $AgentsDir | Out-Null
    New-Item -ItemType Directory -Force -Path $CommandsDir | Out-Null

    # Copy one file, preserving any local customization as <name>.bak.
    #
    # claude-code/README.md tells users to CONFIGURE this tool by editing the very
    # files the installer writes -- the *_PATHS lists, the CUSTOMIZE: blocks, the
    # FAST thresholds. The configuration surface and the install surface are the
    # same files, so overwriting unconditionally makes upgrade == silent config loss.
    $script:BackedUp = 0
    function Install-CcaFile($SourcePath, $DestDir, $Name) {
        $dest = Join-Path $DestDir $Name
        if (Test-Path $dest) {
            $same = (Get-FileHash $SourcePath).Hash -eq (Get-FileHash $dest).Hash
            if (-not $same) {
                Copy-Item $dest -Destination "$dest.bak" -Force
                $script:BackedUp++
                Write-Host "  Updated   $Name -> $DestDir\ (previous version kept as $Name.bak)"
                Copy-Item $SourcePath -Destination $dest -Force
                return
            }
        }
        Copy-Item $SourcePath -Destination $dest -Force
        Write-Host "  Installed $Name -> $DestDir\"
    }

    # Copy agents
    $agents = Get-ChildItem "$SrcDir\agents\cca-*.md" -ErrorAction SilentlyContinue
    if (-not $agents) { throw "no agent files found in $SrcDir\agents\" }
    $agents | ForEach-Object { Install-CcaFile $_.FullName $AgentsDir $_.Name }

    # Warn about pre-existing agents declaring a name we dispatch. Our files are
    # named cca-*.md but their frontmatter `name:` is generic (code-auditor,
    # security-auditor, ...), so a project that already defines one of those names
    # has a collision the cca-*.md glob above cannot see.
    $reserved = '^name:\s*((code|bug|security|perf|doc|numeric|dep|deploy)-auditor|env-validator|fp-check|fix-planner|differential-review|architect-reviewer)\s*$'
    Get-ChildItem "$AgentsDir\*.md" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "cca-*" } |
        ForEach-Object {
            if (Select-String -Path $_.FullName -Pattern $reserved -Quiet) {
                Write-Warning "$($_.Name) declares an agent name CCA-Audit also dispatches; one will shadow the other."
            }
        }

    # Copy orchestrator commands (canonical + DEEP alias)
    $commands = Get-ChildItem "$SrcDir\commands\audit-fix*.md" -ErrorAction SilentlyContinue
    if (-not $commands) { throw "no command files found in $SrcDir\commands\" }
    $commands | ForEach-Object { Install-CcaFile $_.FullName $CommandsDir $_.Name }

    # Install the cca_checks package so the deterministic verifier works.
    #
    # `python` first, matching install.sh and — more importantly — matching what the
    # agent prompts actually invoke (`python -m cca_checks ...`). Installing into a
    # different interpreter yields a "successful" install whose deterministic layer
    # never runs, degrading silently to LLM-only verification.
    $RepoRoot = Split-Path -Parent $SrcDir
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($py -and (Test-Path (Join-Path $RepoRoot "pyproject.toml"))) {
        Write-Host "Installing cca_checks (deterministic verification helpers)..."
        # The `[numeric]` extra is installed by default: the numeric auditor ships
        # unconditionally, and without hypothesis+pytest every numeric claim
        # escalates to UNCERTAIN and cannot be fixed at all on the DEEP tier.
        $target = "$RepoRoot[numeric]"
        # `--user` is refused inside a virtualenv and under PEP 668, both common for
        # a Python project being audited. Plain install first, --user as fallback.
        $pipOut = & $py.Source -m pip install --quiet $target 2>&1
        if ($LASTEXITCODE -ne 0) {
            $pipOut = & $py.Source -m pip install --user --quiet $target 2>&1
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Installed cca_checks[numeric] -> $($py.Source) -m cca_checks"
            & $py.Source -m cca_checks --help 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "'$($py.Source) -m cca_checks' does not run; the deterministic layer will not be used."
            }
        } else {
            Write-Host "  NOTE: cca_checks install failed; /audit-fix falls back to LLM-only verification."
            Write-Host "  ---- pip output (last 10 lines) ----"
            $pipOut | Select-Object -Last 10 | ForEach-Object { Write-Host "  $_" }
        }
    } else {
        Write-Host "  NOTE: python/pip not found; skipping cca_checks. /audit-fix falls back to LLM-only verification."
    }
    Write-Host "  For deterministic checks, also install: pyright, pytest, semgrep (on PATH)."

    Write-Host ""
    if ($script:BackedUp -gt 0) {
        Write-Host "$($script:BackedUp) customized file(s) were updated; the previous versions are saved as *.bak."
    }
    Write-Host "CCA-Audit installed. Run /audit-fix in Claude Code to start."
}
finally {
    if ($CleanupDir -and (Test-Path $CleanupDir)) {
        Remove-Item -Recurse -Force $CleanupDir -ErrorAction SilentlyContinue
    }
}
