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
        $owners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } |
            Where-Object { $_ -gt 0 } |
            Select-Object -Unique)
        return @($owners)
    }
    catch {
        throw "Unable to verify the listener on port $Port. Run stop-aigc.bat from an account that can inspect TCP connections."
    }
}

function Get-ProcessTreeIds {
    param([Parameter(Mandatory)][int]$RootPid)

    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Select-Object ProcessId, ParentProcessId)
    $childrenByParent = @{}
    foreach ($proc in $all) {
        $processId = [int]$proc.ProcessId
        $parentId = [int]$proc.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parentId)) {
            $childrenByParent[$parentId] = New-Object System.Collections.Generic.List[int]
        }
        [void]$childrenByParent[$parentId].Add($processId)
    }

    $ids = New-Object System.Collections.Generic.List[int]
    $seen = @{}
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if ($seen.ContainsKey($current)) {
            continue
        }
        $seen[$current] = $true
        [void]$ids.Add($current)
        if ($childrenByParent.ContainsKey($current)) {
            foreach ($child in $childrenByParent[$current]) {
                $queue.Enqueue([int]$child)
            }
        }
    }
    return @($ids)
}

function Test-IsAigcStudioCommandLine {
    param(
        [Parameter(Mandatory)][string]$Name,
        [AllowEmptyString()][string]$CommandLine,
        [Parameter(Mandatory)][int]$Port
    )

    return $Name -in @("python.exe", "pythonw.exe") -and
        $CommandLine -match "(?i)(^|\s)-m\s+uvicorn\s+api_server:app(\s|$)" -and
        $CommandLine -match "(?i)(^|\s)--port\s+$Port(\s|$)"
}

function Get-ForeignListenerIds {
    param(
        [int[]]$PortOwners,
        [int[]]$TreeIds
    )

    $tree = @{}
    foreach ($id in @($TreeIds)) {
        $tree[[int]$id] = $true
    }
    $foreign = @()
    foreach ($owner in @($PortOwners)) {
        $id = [int]$owner
        if ($id -le 0) {
            continue
        }
        if (-not $tree.ContainsKey($id)) {
            $foreign += $id
        }
    }
    return @($foreign)
}

function Invoke-StopAigcStudio {
    if (-not (Test-Path -LiteralPath $pidPath)) {
        Remove-Item -LiteralPath $portPath -Force -ErrorAction SilentlyContinue
        Write-Host "No AIGC Studio process is managed by this workspace."
        return
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
        return
    }

    $treeIds = @(Get-ProcessTreeIds -RootPid $studioPid)
    $isStudioProcess = $false
    foreach ($treePid in $treeIds) {
        $treeProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $treePid" -ErrorAction SilentlyContinue
        if ($null -eq $treeProcess) {
            continue
        }
        if (Test-IsAigcStudioCommandLine -Name ([string]$treeProcess.Name) -CommandLine ([string]$treeProcess.CommandLine) -Port $studioPort) {
            $isStudioProcess = $true
            break
        }
    }

    if (-not $isStudioProcess) {
        throw "PID $studioPid is not an AIGC Studio server; refusing to stop it. Remove .runtime\studio.pid only after checking the process manually."
    }

    $portOwners = @(Get-ListeningProcessIds -Port $studioPort)
    $foreignOwners = @(Get-ForeignListenerIds -PortOwners $portOwners -TreeIds $treeIds)
    if ($foreignOwners.Count -gt 0) {
        throw "Recorded port $studioPort is owned by PID(s) $($portOwners -join ', '), not exclusively by AIGC Studio PID $studioPid (process tree: $($treeIds -join ', ')); refusing to stop any process."
    }
    if ($portOwners.Count -eq 0) {
        Write-Warning "AIGC Studio PID $studioPid is running, but recorded port $studioPort is not listening. The validated Studio process tree will still be stopped."
    }
    elseif ($studioPid -notin $portOwners) {
        Write-Host "Port $studioPort is held by AIGC Studio child PID(s) $((@($portOwners | Where-Object { $_ -ne $studioPid })) -join ', '); stopping the whole process tree."
    }

    Write-Host "Stopping AIGC Studio (PID $studioPid, port $studioPort)..."
    $stopOrder = @(@($treeIds | Where-Object { $_ -ne $studioPid }) + @($studioPid))
    foreach ($treePid in $stopOrder) {
        Stop-Process -Id $treePid -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(5)
    do {
        Start-Sleep -Milliseconds 200
        $stillRunning = @($treeIds | Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
        $remainingPortOwners = @(Get-ListeningProcessIds -Port $studioPort)
        $remainingTreeListeners = @($remainingPortOwners | Where-Object { $_ -in $treeIds })
    } while (($stillRunning.Count -gt 0 -or $remainingTreeListeners.Count -gt 0) -and (Get-Date) -lt $deadline)

    $stillRunning = @($treeIds | Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
    if ($stillRunning.Count -gt 0) {
        throw "AIGC Studio process tree $($stillRunning -join ', ') did not stop within 5 seconds."
    }

    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $portPath -Force -ErrorAction SilentlyContinue
    $remainingPortOwners = @(Get-ListeningProcessIds -Port $studioPort)
    $remainingForeignOwners = @(Get-ForeignListenerIds -PortOwners $remainingPortOwners -TreeIds @())
    if ($remainingForeignOwners.Count -gt 0) {
        throw "AIGC Studio PID $studioPid stopped, but port $studioPort remains occupied by PID(s) $($remainingForeignOwners -join ', '). The port was not fully released."
    }
    Write-Host "AIGC Studio stopped and port $studioPort is free."
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-StopAigcStudio
}
