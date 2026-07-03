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
        git clone --depth 1 --branch $RepoRef $RepoUrl (Join-Path $CleanupDir "repo") 2>&1 | Out-Null
        $SrcDir = Join-Path (Join-Path $CleanupDir "repo") "claude-code"
    }

    $AgentsDir = ".claude\agents"
    $CommandsDir = ".claude\commands"
    New-Item -ItemType Directory -Force -Path $AgentsDir | Out-Null
    New-Item -ItemType Directory -Force -Path $CommandsDir | Out-Null

    # Copy agents
    $agents = Get-ChildItem "$SrcDir\agents\cca-*.md" -ErrorAction SilentlyContinue
    if (-not $agents) { throw "no agent files found in $SrcDir\agents\" }
    $agents | ForEach-Object {
        Copy-Item $_.FullName -Destination "$AgentsDir\$($_.Name)"
        Write-Host "  Installed $($_.Name) -> $AgentsDir\"
    }

    # Copy orchestrator commands (canonical + DEEP alias)
    $commands = Get-ChildItem "$SrcDir\commands\audit-fix*.md" -ErrorAction SilentlyContinue
    if (-not $commands) { throw "no command files found in $SrcDir\commands\" }
    $commands | ForEach-Object {
        Copy-Item $_.FullName -Destination "$CommandsDir\$($_.Name)"
        Write-Host "  Installed $($_.Name) -> $CommandsDir\"
    }

    Write-Host ""
    Write-Host "CCA-Audit installed. Run /audit-fix in Claude Code to start."
}
finally {
    if ($CleanupDir -and (Test-Path $CleanupDir)) {
        Remove-Item -Recurse -Force $CleanupDir -ErrorAction SilentlyContinue
    }
}
