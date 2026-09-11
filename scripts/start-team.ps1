[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$currentPane = (& herdr pane current --current | ConvertFrom-Json).result.pane

if ($env:HERDR_ENV -ne "1") {
    throw "This script must run inside a Herdr-managed pane."
}
if ((Resolve-Path -LiteralPath $currentPane.cwd).Path -ne (Resolve-Path -LiteralPath $projectRoot).Path) {
    throw "Run this script from the AIGC project workspace."
}

& herdr agent rename $currentPane.pane_id aigc-lead-codex | Out-Null
& herdr pane rename $currentPane.pane_id "AIGC Lead Codex" | Out-Null

function Invoke-AntiLoop {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $stateDir = Join-Path $projectRoot ".agents\runtime\herdr-anti-loop"
    $python = if ($env:AIGC_PYTHON) { $env:AIGC_PYTHON } else { "python" }
    Push-Location $projectRoot
    try {
        $output = & $python -m harness.anti_loop --state-dir $stateDir @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Anti-loop guard rejected: $output"
        }
        return $output
    } finally {
        Pop-Location
    }
}

& (Join-Path $PSScriptRoot "herdr-control.ps1") init | Out-Null
$workspaceId = if ($env:HERDR_WORKSPACE_ID) { $env:HERDR_WORKSPACE_ID } else { "workspace-local" }
Invoke-AntiLoop @("bootstrap", "--workspace-id", $workspaceId, "--owner", "aigc-lead-codex") | Out-Null
try {
    & (Join-Path $PSScriptRoot "herdr-control.ps1") task create `
        -Id TEAM-ROOT `
        -Owner aigc-lead-codex `
        -Paths @("docs/architecture", "team") `
        -Acceptance @("Each handoff has one owner, a named anti-loop task, and either terminal evidence or a Lead escalation.") `
        -Verify @("python -m harness.anti_loop status") | Out-Null
} catch {
    if ("$($_.Exception.Message)" -notmatch "already exists") {
        throw
    }
}

function New-AigcAgentPane {
    param(
        [Parameter(Mandatory)][string]$SourcePane,
        [Parameter(Mandatory)][ValidateSet("right", "down")][string]$Direction,
        [Parameter(Mandatory)][string]$AgentName,
        [Parameter(Mandatory)][ValidateSet("codex", "omp")][string]$Kind,
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$RolePrompt
    )

    $knownAgent = ((& herdr agent list | ConvertFrom-Json).result.agents |
        Where-Object {
            $name = $_.PSObject.Properties["name"]
            $cwd = $_.PSObject.Properties["cwd"]
            $null -ne $name -and $null -ne $cwd -and
                $name.Value -eq $AgentName -and $cwd.Value -eq $projectRoot
        } |
        Select-Object -First 1)
    if ($null -ne $knownAgent) {
        return $knownAgent.pane_id
    }

    $split = & herdr pane split --pane $SourcePane --direction $Direction --cwd $projectRoot --no-focus |
        ConvertFrom-Json
    $paneId = $split.result.pane.pane_id
    & herdr pane rename $paneId $Label | Out-Null

    if ($Kind -eq "omp") {
        & herdr agent start $AgentName --kind omp --pane $paneId --timeout 60000 -- --cwd $projectRoot --append-system-prompt $RolePrompt | Out-Null
    } else {
        & herdr agent start $AgentName --kind codex --pane $paneId --timeout 60000 | Out-Null
    }
    return $paneId
}

$guardPrompt = "All work is protected by the AIGC Herdr Anti-Loop Guard. TEAM-ROOT is the live root run owned by AIGC Lead Codex. For every next action, state internally: objective, one atomic action, expected_progress, new_hypothesis when retrying, and stop_condition. Do not repeat a materially identical action after a permanent error. Stop after two completed attempts without verifiable progress and report the blocker to AIGC Lead Codex. Wait only for a named anti-loop task_id with a deadline. Coordination progress notes are not anti-loop progress."
$leadPrompt = "You are AIGC Lead Codex, the orchestrator. Read AGENTS.md and docs/architecture/agent-team-anti-loop-design.md. Own TEAM-ROOT. Admit every dispatch and resume through python -m harness.anti_loop. Create one child run per worker handoff; do not start a second root run. Resume paused runs only with a new hypothesis. $guardPrompt"
$buildPrompt = "You are AIGC Build Codex, an implementation worker. Read AGENTS.md, stay within assigned scope, and wait for tasks from AIGC Lead Codex. $guardPrompt"
$productPrompt = "You are AIGC Product OMP, a product research worker. Read AGENTS.md, use evidence, edit only docs/product and research by default, and wait for tasks from the lead. $guardPrompt"
$qualityPrompt = "You are AIGC Quality OMP, an independent reviewer. Read AGENTS.md, treat product source as read-only unless assigned a fix, and wait for review tasks from the lead. $guardPrompt"

$buildPane = New-AigcAgentPane -SourcePane $currentPane.pane_id -Direction right -AgentName "aigc-build-codex" -Kind codex -Label "AIGC Build Codex" -RolePrompt $buildPrompt
$productPane = New-AigcAgentPane -SourcePane $currentPane.pane_id -Direction down -AgentName "aigc-product-omp" -Kind omp -Label "AIGC Product OMP" -RolePrompt $productPrompt
$qualityPane = New-AigcAgentPane -SourcePane $buildPane -Direction down -AgentName "aigc-quality-omp" -Kind omp -Label "AIGC Quality OMP" -RolePrompt $qualityPrompt

& herdr agent prompt aigc-lead-codex $leadPrompt | Out-Null
& herdr agent prompt aigc-build-codex $buildPrompt | Out-Null
& herdr agent prompt aigc-product-omp $productPrompt | Out-Null
& herdr agent prompt aigc-quality-omp $qualityPrompt | Out-Null

$supervisor = Join-Path $projectRoot "herdr-plugins\supervisor\run.ps1"
if (Test-Path -LiteralPath $supervisor -PathType Leaf) {
    & $supervisor start | Out-Null
}

[ordered]@{
    lead = $currentPane.pane_id
    build = $buildPane
    product = $productPane
    quality = $qualityPane
} | ConvertTo-Json

