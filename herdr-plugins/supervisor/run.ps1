[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("start", "status", "reconcile", "stop", "event")][string]$Command
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$stateDir = if ($env:AIGC_SUPERVISOR_STATE_DIR) { $env:AIGC_SUPERVISOR_STATE_DIR } else { Join-Path $projectRoot ".agents/runtime/herdr-supervisor" }
$python = if ($env:AIGC_PYTHON) { $env:AIGC_PYTHON } else { "python" }
$logPath = Join-Path $stateDir "supervisor.log"

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

function Invoke-Supervisor {
    param([string[]]$Arguments)
    & $python -m harness.supervisor --project-root $projectRoot --state-dir $stateDir @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Push-Location -LiteralPath $projectRoot
try {
    switch ($Command) {
        "start" {
            $lockPath = Join-Path $stateDir "supervisor.lock"
            if (Test-Path -LiteralPath $lockPath -PathType Leaf) {
                try {
                    $pidValue = [int]([System.IO.File]::ReadAllText($lockPath)).Trim()
                    Get-Process -Id $pidValue -ErrorAction Stop | Out-Null
                    Write-Output "Supervisor already running (pid $pidValue)."
                    break
                } catch {
                    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
                }
            }
            $arguments = @("-m", "harness.supervisor", "--project-root", $projectRoot, "--state-dir", $stateDir, "run")
            $errorLogPath = Join-Path $stateDir "supervisor.error.log"
            $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $logPath -RedirectStandardError $errorLogPath -PassThru
            Write-Output (ConvertTo-Json ([ordered]@{ ok = $true; started = $true; pid = $process.Id; stateDir = $stateDir }) -Compress)
        }
        "status" { Invoke-Supervisor @("status") }
        "stop" {
            $processes = @(Get-CimInstance Win32_Process | Where-Object {
                $_.Name -eq "python.exe" -and $_.CommandLine -match "-m harness\.supervisor" -and $_.CommandLine -match [regex]::Escape($projectRoot)
            })
            if ($processes.Count -eq 0) { Write-Output "Supervisor is not running."; break }
            foreach ($process in $processes) {
                Stop-Process -Id ([int]$process.ProcessId) -ErrorAction Stop
                Write-Output "Supervisor stop requested (pid $($process.ProcessId))."
            }
        }
    }
} finally {
    Pop-Location
}
