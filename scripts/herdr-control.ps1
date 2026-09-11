[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("init", "task", "status", "checkpoint", "watchdog", "audit", "guard")]
    [string]$Command,

    [Parameter(Position = 1)]
    [ValidateSet("create", "transition", "progress", "error", "archive")]
    [string]$Action,

    [string]$Id,
    [string]$Owner,
    [string[]]$Paths,
    [string[]]$Acceptance,
    [string[]]$Verify,
    [string[]]$Dependencies = @(),
    [string]$To,
    [string]$SubmissionId,
    [string]$NativeThreadId,
    [string]$HerdrDisplayId,
    [int]$AgentPid = 0,
    [string]$Note,
    [string]$Evidence,
    [string]$Code,
    [string]$Stage,
    [string]$StageStatus,
    [Nullable[bool]]$GatePassed,
    [string]$ActiveTaskId,
    [string]$Summary,
    [string[]]$NextActions,
    [string[]]$Blockers,
    [int]$EventCount = -1,
    [int]$TokenEstimate = -1,
    [Nullable[bool]]$RotationRequested,
    [string]$RotationReason,
    [string]$SessionPath,
    [string]$Operation,
    [string]$Target,
    [string]$CellId,
    [string]$Message,
    [string]$Contract
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$shared = Join-Path $projectRoot "..\agent_bus\coordination\herdr-control.ps1"

$forward = @{
    ProjectRoot = $projectRoot
    Command = $Command
}
if ($PSBoundParameters.ContainsKey("Action")) { $forward.Action = $Action }
foreach ($name in @(
    "Id", "Owner", "Paths", "Acceptance", "Verify", "Dependencies", "To",
    "SubmissionId", "NativeThreadId", "HerdrDisplayId", "AgentPid", "Note",
    "Evidence", "Code", "Stage", "StageStatus", "GatePassed", "ActiveTaskId",
    "Summary", "NextActions", "Blockers", "EventCount", "TokenEstimate",
    "RotationRequested", "RotationReason", "SessionPath", "Operation",
    "Target", "CellId", "Message", "Contract"
)) {
    if ($PSBoundParameters.ContainsKey($name)) {
        $forward[$name] = $PSBoundParameters[$name]
    }
}

$output = & $shared @forward
$exitCode = if (Get-Variable LASTEXITCODE -ErrorAction SilentlyContinue) { [int]$LASTEXITCODE } else { 0 }

function Invoke-AntiLoopSync {
    $stateDir = Join-Path $projectRoot ".agents\runtime\herdr-anti-loop"
    $tasksFile = Join-Path $projectRoot ".agents\coordination\tasks.json"
    $workspaceId = if ($env:HERDR_WORKSPACE_ID) { $env:HERDR_WORKSPACE_ID } else { "workspace-local" }
    $python = if ($env:AIGC_PYTHON) { $env:AIGC_PYTHON } else { "python" }
    $syncArgs = @(
        "-m", "harness.anti_loop",
        "--state-dir", $stateDir,
        "sync",
        "--workspace-id", $workspaceId
    )
    if (Test-Path -LiteralPath $tasksFile -PathType Leaf) {
        $syncArgs += @("--tasks-file", $tasksFile)
    }
    Push-Location $projectRoot
    try {
        $syncOutput = & $python @syncArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Anti-loop sync failed: $syncOutput"
        }
    } finally {
        Pop-Location
    }
}

if ($exitCode -eq 0) {
    $shouldSync = ($Command -eq "init") -or ($Command -eq "task" -and $Action -in @("create", "transition", "archive"))
    if ($shouldSync) {
        try {
            Invoke-AntiLoopSync
        } catch {
            if ($null -ne $output -and "$output".Length -gt 0) {
                Write-Output $output
            }
            Write-Error $_.Exception.Message
            exit 1
        }
    }
}

if ($null -ne $output -and "$output".Length -gt 0) {
    Write-Output $output
}
exit $exitCode
