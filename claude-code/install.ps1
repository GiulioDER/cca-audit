# CCA-Audit installer for Claude Code (Windows)
# Copies agent and command files to .claude/ in the current project

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$AgentsDir = ".claude\agents"
$CommandsDir = ".claude\commands"

New-Item -ItemType Directory -Force -Path $AgentsDir | Out-Null
New-Item -ItemType Directory -Force -Path $CommandsDir | Out-Null

# Copy agents
Get-ChildItem "$ScriptDir\agents\cca-*.md" | ForEach-Object {
    Copy-Item $_.FullName -Destination "$AgentsDir\$($_.Name)"
    Write-Host "  Installed $($_.Name) -> $AgentsDir\"
}

# Copy orchestrator commands (v1 + v2)
Get-ChildItem "$ScriptDir\commands\audit-fix*.md" | ForEach-Object {
    Copy-Item $_.FullName -Destination "$CommandsDir\$($_.Name)"
    Write-Host "  Installed $($_.Name) -> $CommandsDir\"
}

Write-Host ""
Write-Host "CCA-Audit installed. Run /audit-fix (or /audit-fix-v2) in Claude Code to start."
