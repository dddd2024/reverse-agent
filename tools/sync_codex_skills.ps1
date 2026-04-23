param(
    [string]$SourceRoot,
    [string]$DestinationRoot,
    [string[]]$SkillName = @()
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $SourceRoot) {
    $SourceRoot = Join-Path $repoRoot ".codex-skills"
}
if (-not $DestinationRoot) {
    $codexHome = $env:CODEX_HOME
    if (-not $codexHome) {
        $codexHome = Join-Path $env:USERPROFILE ".codex"
    }
    $DestinationRoot = Join-Path $codexHome "skills"
}

if (-not (Test-Path -LiteralPath $SourceRoot)) {
    throw "Skill source root not found: $SourceRoot"
}

New-Item -ItemType Directory -Force -Path $DestinationRoot | Out-Null

$skillDirs = Get-ChildItem -LiteralPath $SourceRoot -Directory | Where-Object {
    Test-Path -LiteralPath (Join-Path $_.FullName "SKILL.md")
}
if ($SkillName.Count -gt 0) {
    $wanted = @{}
    foreach ($name in $SkillName) {
        $wanted[$name] = $true
    }
    $skillDirs = $skillDirs | Where-Object { $wanted.ContainsKey($_.Name) }
}

foreach ($skillDir in $skillDirs) {
    $dest = Join-Path $DestinationRoot $skillDir.Name
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -LiteralPath (Join-Path $skillDir.FullName "*") -Destination $dest -Recurse -Force
    Write-Host "Synced skill $($skillDir.Name) -> $dest"
}

if (-not $skillDirs) {
    Write-Host "No skills matched."
}
