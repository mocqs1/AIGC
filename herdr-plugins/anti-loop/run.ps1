[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("status", "sweep", "event")][string]$Command
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$stateDir = if ($env:AIGC_ANTI_LOOP_STATE_DIR) { $env:AIGC_ANTI_LOOP_STATE_DIR } else { Join-Path $projectRoot ".agents/runtime/herdr-anti-loop" }
$python = if ($env:AIGC_PYTHON) { $env:AIGC_PYTHON } else { "python" }
$workspace = if ($env:HERDR_WORKSPACE_ID) { $env:HERDR_WORKSPACE_ID } else { "workspace-local" }

function Invoke-Guard {
    param([string[]]$Arguments)
    & $python -m harness.anti_loop --state-dir $stateDir @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Push-Location -LiteralPath $projectRoot
try {
    switch ($Command) {
        "status" { Invoke-Guard @("status", "--workspace-id", $workspace) }
        "sweep" { Invoke-Guard @("sweep", "--workspace-id", $workspace) }
        "event" {
            $payload = if ($env:HERDR_PLUGIN_EVENT_JSON) { $env:HERDR_PLUGIN_EVENT_JSON } else { "{}" }
            Invoke-Guard @("event", "--workspace-id", $workspace, "--payload", $payload)
        }
    }
} finally {
    Pop-Location
}
