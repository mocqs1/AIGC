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

$guardPrompt = "All work is protected by the AIGC Herdr Anti-Loop Guard. For every next action, state internally: objective, one atomic action, expected_progress, new_hypothesis when retrying, and stop_condition. Do not repeat a materially identical action after a permanent error. Stop after two completed attempts without verifiable progress and report the blocker to AIGC Lead Codex. Wait only for a named dependency with a deadline."
$buildPrompt = "You are AIGC Build Codex, an implementation worker. Read AGENTS.md, stay within assigned scope, and wait for tasks from AIGC Lead Codex. $guardPrompt"
$productPrompt = "You are AIGC Product OMP, a product research worker. Read AGENTS.md, use evidence, edit only docs/product and research by default, and wait for tasks from the lead. $guardPrompt"
$qualityPrompt = "You are AIGC Quality OMP, an independent reviewer. Read AGENTS.md, treat product source as read-only unless assigned a fix, and wait for review tasks from the lead. $guardPrompt"

$buildPane = New-AigcAgentPane -SourcePane $currentPane.pane_id -Direction right -AgentName "aigc-build-codex" -Kind codex -Label "AIGC Build Codex" -RolePrompt $buildPrompt
$productPane = New-AigcAgentPane -SourcePane $currentPane.pane_id -Direction down -AgentName "aigc-product-omp" -Kind omp -Label "AIGC Product OMP" -RolePrompt $productPrompt
$qualityPane = New-AigcAgentPane -SourcePane $buildPane -Direction down -AgentName "aigc-quality-omp" -Kind omp -Label "AIGC Quality OMP" -RolePrompt $qualityPrompt

& herdr agent prompt aigc-build-codex $buildPrompt | Out-Null

[ordered]@{
    lead = $currentPane.pane_id
    build = $buildPane
    product = $productPane
    quality = $qualityPane
} | ConvertTo-Json

