[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot ".runtime"
$pidPath = Join-Path $runtimeRoot "studio.pid"
$portPath = Join-Path $runtimeRoot "studio.port"

function Get-ListeningProcessIds {
    param([Parameter(Mandatory)][int]$Port)

    if ($null -eq (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        throw "Get-NetTCPConnection is unavailable, so the listener on port $Port cannot be verified safely."
    }
    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
    }
    catch {
        throw "Unable to verify the listener on port $Port. Run stop-aigc.bat from an account that can inspect TCP connections."
    }
}

if (-not (Test-Path -LiteralPath $pidPath)) {
    Remove-Item -LiteralPath $portPath -Force -ErrorAction SilentlyContinue
    Write-Host "No AIGC Studio process is managed by this workspace."
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
$studioPid = 0
if (-not [int]::TryParse($pidText, [ref]$studioPid) -or $studioPid -le 0) {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $portPath -Force -ErrorAction SilentlyContinue
    throw "The AIGC Studio PID file is invalid. It was removed; start Studio again to recreate it."
}

$studioPort = 0
if (-not (Test-Path -LiteralPath $portPath) -or -not [int]::TryParse((Get-Content -LiteralPath $portPath -Raw).Trim(), [ref]$studioPort) -or $studioPort -le 0 -or $studioPort -gt 65535) {
    throw "The AIGC Studio port record is missing or invalid; refusing to stop an unverified process."
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId = $studioPid" -ErrorAction SilentlyContinue
if ($null -eq $process) {
    $stalePortOwners = @(Get-ListeningProcessIds -Port $studioPort)
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $portPath -Force -ErrorAction SilentlyContinue
    if ($stalePortOwners.Count -gt 0) {
        Write-Warning "AIGC Studio PID $studioPid is already stopped, but recorded port $studioPort is now owned by PID(s) $($stalePortOwners -join ', '). The unrelated listener was not touched."
    }
    else {
        Write-Host "AIGC Studio is already stopped (stale PID $studioPid removed; port $studioPort is free)."
    }
    exit 0
}

$commandLine = [string]$process.CommandLine
$isStudioProcess = $process.Name -in @("python.exe", "pythonw.exe") -and
    $commandLine -match "(?i)(^|\s)-m\s+uvicorn\s+api_server:app(\s|$)" -and
    $commandLine -match "(?i)(^|\s)--port\s+$studioPort(\s|$)"

if (-not $isStudioProcess) {
    throw "PID $studioPid is not an AIGC Studio server; refusing to stop it. Remove .runtime\studio.pid only after checking the process manually."
}

$portOwners = @(Get-ListeningProcessIds -Port $studioPort)
$foreignOwners = @($portOwners | Where-Object { $_ -ne $studioPid })
if ($foreignOwners.Count -gt 0 -or ($portOwners.Count -gt 0 -and $studioPid -notin $portOwners)) {
    throw "Recorded port $studioPort is owned by PID(s) $($portOwners -join ', '), not exclusively by AIGC Studio PID $studioPid; refusing to stop any process."
}
if ($portOwners.Count -eq 0) {
    Write-Warning "AIGC Studio PID $studioPid is running, but recorded port $studioPort is not listening. The validated Studio process will still be stopped."
}

Write-Host "Stopping AIGC Studio (PID $studioPid, port $studioPort)..."
Stop-Process -Id $studioPid -Force
$deadline = (Get-Date).AddSeconds(5)
do {
    Start-Sleep -Milliseconds 200
    $stillRunning = Get-Process -Id $studioPid -ErrorAction SilentlyContinue
    $remainingPortOwners = @(Get-ListeningProcessIds -Port $studioPort)
} while (($null -ne $stillRunning -or $remainingPortOwners.Count -gt 0) -and (Get-Date) -lt $deadline)

if ($null -ne (Get-Process -Id $studioPid -ErrorAction SilentlyContinue)) {
    throw "AIGC Studio process $studioPid did not stop within 5 seconds."
}

Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $portPath -Force -ErrorAction SilentlyContinue
$remainingPortOwners = @(Get-ListeningProcessIds -Port $studioPort)
if ($remainingPortOwners.Count -gt 0) {
    throw "AIGC Studio PID $studioPid stopped, but port $studioPort remains occupied by PID(s) $($remainingPortOwners -join ', '). The port was not fully released."
}
Write-Host "AIGC Studio stopped and port $studioPort is free."
