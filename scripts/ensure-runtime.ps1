# Requires: Windows PowerShell 5.1+
# Detects Python 3.11+ and Node.js 18+, installing them when missing.
# Usage: powershell -File scripts/ensure-runtime.ps1 [-Probe] [-SkipInstall] [-ProjectRoot PATH]

[CmdletBinding()]
param(
    [switch]$Probe,
    [switch]$SkipInstall,
    [string]$ProjectRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:PythonMinVersion = [Version]"3.11"
$script:NodeMinMajor = 18
$script:PythonWingetId = "Python.Python.3.12"
$script:NodeWingetId = "OpenJS.NodeJS.LTS"
$script:PythonInstallerUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
$script:NodeInstallerUrl = "https://nodejs.org/dist/v22.20.0/node-v22.20.0-x64.msi"

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($machine, $user, $env:Path) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $env:Path = ($parts -join ";")
}

function Test-PythonWindowsStoreStub {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Path
    )
    return ($Name -match '^(pythonw?|python3|pip)$') -and ($Path -match '(?i)\\WindowsApps\\')
}

function Get-CommandPath {
    param(
        [Parameter(Mandatory)][string]$Name,
        [switch]$AllowWindowsApps
    )
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }
    $path = [string]$command.Source
    if (-not $path -or -not (Test-Path -LiteralPath $path)) {
        return $null
    }
    if (-not $AllowWindowsApps -and (Test-PythonWindowsStoreStub -Name $Name -Path $path)) {
        return $null
    }
    return $path
}

function Get-PythonVersion {
    param([Parameter(Mandatory)][string]$Exe)
    try {
        $output = & $Exe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
            return $null
        }
        return [Version]($output.ToString().Trim())
    }
    catch {
        return $null
    }
}

function Test-UsablePython {
    param([Parameter(Mandatory)][string]$Exe)
    $version = Get-PythonVersion -Exe $Exe
    return $null -ne $version -and $version -ge $script:PythonMinVersion
}

function Get-NodeMajor {
    param([Parameter(Mandatory)][string]$Exe)
    try {
        $output = & $Exe -e "process.stdout.write(String(process.versions.node.split('.')[0]))" 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
            return $null
        }
        return [int]$output.ToString().Trim()
    }
    catch {
        return $null
    }
}

function Test-UsableNode {
    param([Parameter(Mandatory)][string]$Exe)
    $major = Get-NodeMajor -Exe $Exe
    return $null -ne $major -and $major -ge $script:NodeMinMajor
}

function Find-PythonInstall {
    $candidates = New-Object System.Collections.Generic.List[string]
    $py = Get-CommandPath -Name "py"
    if (-not $py) {
        $py = Get-CommandPath -Name "py.exe"
    }
    if ($py) {
        foreach ($spec in @("-3.12", "-3.13", "-3.11")) {
            try {
                $resolved = & $py $spec -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($resolved)) {
                    $candidates.Add($resolved.ToString().Trim()) | Out-Null
                }
            }
            catch {
            }
        }
    }
    foreach ($name in @("python", "python3")) {
        $path = Get-CommandPath -Name $name
        if ($path) {
            $candidates.Add($path) | Out-Null
        }
    }
    $roots = @(
        (Join-Path $env:LocalAppData "Programs\Python"),
        ${env:ProgramFiles},
        ${env:ProgramFiles(x86)}
    )
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) {
            continue
        }
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Python3(1[1-9]|[2-9]\d)$' } |
            ForEach-Object {
                $exe = Join-Path $_.FullName "python.exe"
                if (Test-Path -LiteralPath $exe) {
                    $candidates.Add($exe) | Out-Null
                }
            }
    }
    foreach ($candidate in $candidates) {
        if ((Test-Path -LiteralPath $candidate) -and (Test-UsablePython -Exe $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Find-NodeInstall {
    $path = Get-CommandPath -Name "node"
    if ($path -and (Test-UsableNode -Exe $path)) {
        return $path
    }
    $guesses = @(
        (Join-Path ${env:ProgramFiles} "nodejs\node.exe"),
        (Join-Path $env:LocalAppData "Programs\nodejs\node.exe")
    )
    foreach ($guess in $guesses) {
        if ((Test-Path -LiteralPath $guess) -and (Test-UsableNode -Exe $guess)) {
            return $guess
        }
    }
    return $null
}

function Find-NpmInstall {
    param([string]$NodePath)
    $guesses = New-Object System.Collections.Generic.List[string]
    if ($NodePath) {
        $guesses.Add((Join-Path (Split-Path -Parent $NodePath) "npm.cmd")) | Out-Null
    }
    foreach ($name in @("npm.cmd", "npm")) {
        $path = Get-CommandPath -Name $name
        if ($path) {
            $guesses.Add($path) | Out-Null
        }
    }
    $guesses.Add((Join-Path ${env:ProgramFiles} "nodejs\npm.cmd")) | Out-Null
    $guesses.Add((Join-Path $env:LocalAppData "Programs\nodejs\npm.cmd")) | Out-Null
    foreach ($guess in $guesses) {
        if ($guess -and (Test-Path -LiteralPath $guess)) {
            return $guess
        }
    }
    return $null
}

function Invoke-WingetInstall {
    param([Parameter(Mandatory)][string]$PackageId)
    $winget = Get-CommandPath -Name "winget" -AllowWindowsApps
    if (-not $winget) {
        return $false
    }
    $common = @(
        "install", "--id", $PackageId, "-e", "--wait",
        "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    Write-Host "Installing $PackageId with winget..."
    $null = & $winget @common --scope user
    if ($LASTEXITCODE -in @(0, -1978335189)) {
        Update-SessionPath
        return $true
    }
    Write-Host "User-scope install was not available; retrying $PackageId..."
    $null = & $winget @common
    if ($LASTEXITCODE -in @(0, -1978335189)) {
        Update-SessionPath
        return $true
    }
    return $false
}
function Get-BootstrapDirectory {
    $path = Join-Path $env:TEMP "aigc-runtime"
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    return $path
}

function Invoke-WebDownload {
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$Destination
    )
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    if (-not (Test-Path -LiteralPath $Destination) -or (Get-Item -LiteralPath $Destination).Length -lt 1MB) {
        throw "Downloaded installer is missing or too small: $Destination"
    }
}

function Install-PythonRuntime {
    if (Invoke-WingetInstall -PackageId $script:PythonWingetId) {
        return
    }
    $installer = Join-Path (Get-BootstrapDirectory) "python-3.12.10-amd64.exe"
    Invoke-WebDownload -Url $script:PythonInstallerUrl -Destination $installer
    Write-Host "Installing Python 3.12 for the current user..."
    $process = Start-Process -FilePath $installer -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_pip=1",
        "Include_launcher=1",
        "Include_test=0",
        "SimpleInstall=1"
    ) -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Python installer failed with exit code $($process.ExitCode). Install Python 3.11+ from https://www.python.org/downloads/windows/ and run start-aigc.bat again."
    }
    Update-SessionPath
}

function Install-NodeRuntime {
    if (Invoke-WingetInstall -PackageId $script:NodeWingetId) {
        return
    }
    $installer = Join-Path (Get-BootstrapDirectory) "node-v22.20.0-x64.msi"
    Invoke-WebDownload -Url $script:NodeInstallerUrl -Destination $installer
    Write-Host "Installing Node.js LTS..."
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList @(
        "/i", $installer, "/qn", "/norestart"
    ) -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Node.js installer failed with exit code $($process.ExitCode). Install Node.js 18+ LTS from https://nodejs.org/ and run start-aigc.bat again."
    }
    Update-SessionPath
}

function New-ProjectVenv {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Python
    )
    $venvDir = Join-Path $Root ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if ((Test-Path -LiteralPath $venvPython) -and (Test-UsablePython -Exe $venvPython)) {
        return $venvPython
    }
    if (Test-Path -LiteralPath $venvDir) {
        Write-Host "Replacing unusable project virtual environment..."
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }
    Write-Host "Creating project virtual environment..."
    $null = & $Python -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-UsablePython -Exe $venvPython)) {
        throw "Failed to create a Python 3.11+ virtual environment at $venvDir"
    }
    return $venvPython
}

function Get-RuntimeProbe {
    param([Parameter(Mandatory)][string]$Root)
    Update-SessionPath
    $python = Find-PythonInstall
    $node = Find-NodeInstall
    $npm = Find-NpmInstall -NodePath $node
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    return [ordered]@{
        python = @{
            path = $python
            version = if ($python) { (Get-PythonVersion -Exe $python).ToString() } else { $null }
            usable = [bool]$python
        }
        node = @{
            path = $node
            major = if ($node) { Get-NodeMajor -Exe $node } else { $null }
            usable = [bool]$node
        }
        npm = @{
            path = $npm
            usable = [bool]$npm
        }
        venv = @{
            path = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { $null }
            usable = (Test-Path -LiteralPath $venvPython) -and (Test-UsablePython -Exe $venvPython)
        }
    }
}

function Get-AigcRuntime {
    param(
        [Parameter(Mandatory)][string]$Root,
        [switch]$NoInstall
    )
    if ($env:OS -ne "Windows_NT") {
        throw "The one-click installer currently supports Windows only."
    }

    Update-SessionPath
    $python = Find-PythonInstall
    if (-not $python) {
        if ($NoInstall) {
            throw "Python 3.11+ was not found."
        }
        Write-Host "Python 3.11+ was not found. Installing it now..."
        Install-PythonRuntime
        $python = Find-PythonInstall
        if (-not $python) {
            throw "Python 3.11+ is still missing after installation. Close this window, open a new one, and run start-aigc.bat again."
        }
    }

    $node = Find-NodeInstall
    if (-not $node) {
        if ($NoInstall) {
            throw "Node.js 18+ was not found."
        }
        Write-Host "Node.js 18+ was not found. Installing it now..."
        Install-NodeRuntime
        $node = Find-NodeInstall
        if (-not $node) {
            throw "Node.js 18+ is still missing after installation. Close this window, open a new one, and run start-aigc.bat again."
        }
    }

    $npm = Find-NpmInstall -NodePath $node
    if (-not $npm) {
        throw "npm was not found. Reinstall Node.js LTS and keep the npm feature enabled."
    }

    $venvPython = New-ProjectVenv -Root $Root -Python $python
    return [ordered]@{
        python = $python
        pythonVersion = (Get-PythonVersion -Exe $python).ToString()
        venvPython = $venvPython
        node = $node
        nodeMajor = Get-NodeMajor -Exe $node
        npm = $npm
    }
}

$root = if ($ProjectRoot) {
    (Resolve-Path -LiteralPath $ProjectRoot).Path
} else {
    Split-Path -Parent $PSScriptRoot
}

if ($Probe) {
    Get-RuntimeProbe -Root $root | ConvertTo-Json -Compress
    exit 0
}

Get-AigcRuntime -Root $root -NoInstall:$SkipInstall | ConvertTo-Json -Compress
