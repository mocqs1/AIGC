[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot "web"
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$runtimeRoot = Join-Path $projectRoot ".runtime"
$portPath = Join-Path $runtimeRoot "studio.port"
$studioPort = 0
$studioUrl = ""
$healthUrl = ""
$openApiUrl = ""
$script:StudioPortMin = 49152
$script:StudioPortMax = 65535
$script:ReservedStudioPorts = @(
    8000, 8080, 8081, 8443, 8888, 9000, 9090, 9200, 9300, 9418,
    27017, 3306, 5432, 6379, 11211, 1433, 1521, 5000, 5001, 5173, 3000, 3001
)

function Set-StudioEndpoint {
    param([Parameter(Mandatory)][int]$Port)
    $script:studioPort = $Port
    $script:studioUrl = "http://127.0.0.1:$Port"
    $script:healthUrl = "$script:studioUrl/api/health"
    $script:openApiUrl = "$script:studioUrl/openapi.json"
}

function Test-PortInUse {
    param([Parameter(Mandatory)][int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $connection.Wait(500)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-ReservedStudioPort {
    param([Parameter(Mandatory)][int]$Port)
    return $Port -in $script:ReservedStudioPorts
}

function Test-StudioPortCandidate {
    param([Parameter(Mandatory)][int]$Port)
    if ($Port -lt $script:StudioPortMin -or $Port -gt $script:StudioPortMax) {
        return $false
    }
    if (Test-ReservedStudioPort -Port $Port) {
        return $false
    }
    return -not (Test-PortInUse -Port $Port)
}

function Find-StudioPort {
    $rng = [System.Random]::new()
    $span = $script:StudioPortMax - $script:StudioPortMin + 1
    for ($attempt = 0; $attempt -lt 64; $attempt++) {
        $candidate = $rng.Next($script:StudioPortMin, $script:StudioPortMax + 1)
        if (Test-StudioPortCandidate -Port $candidate) {
            return $candidate
        }
    }
    $offset = $rng.Next(0, $span)
    for ($index = 0; $index -lt $span; $index++) {
        $candidate = $script:StudioPortMin + (($offset + $index) % $span)
        if (Test-StudioPortCandidate -Port $candidate) {
            return $candidate
        }
    }
    throw "No available local port was found between $($script:StudioPortMin) and $($script:StudioPortMax)."
}

function Test-AigcStudio {
    if ($script:studioPort -le 0 -or [string]::IsNullOrWhiteSpace($script:healthUrl)) {
        return $false
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        $metadata = Invoke-RestMethod -Uri $openApiUrl -TimeoutSec 2
        $pageContent = Invoke-RestMethod -Uri $studioUrl -TimeoutSec 2
        return $health.status -eq "ok" -and
            $metadata.info.title -eq "AIGC Studio" -and
            $pageContent -match "<title>AIGC Studio</title>"
    }
    catch {
        return $false
    }
}

function Open-Studio {
    if ($NoBrowser) {
        return
    }
    try {
        Start-Process $studioUrl | Out-Null
    }
    catch {
        Write-Warning "Studio is ready, but the browser could not be opened automatically. Open $studioUrl manually."
    }
}

function Invoke-RuntimeEnsure {
    $ensureScript = Join-Path $PSScriptRoot "ensure-runtime.ps1"
    if (-not (Test-Path -LiteralPath $ensureScript)) {
        throw "Missing runtime bootstrap script: $ensureScript"
    }
    $output = & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $ensureScript -ProjectRoot $projectRoot 2>&1
    $exitCode = $LASTEXITCODE
    $jsonLine = $null
    foreach ($line in @($output)) {
        $text = [string]$line
        if ($text -match '^\s*\{') {
            $jsonLine = $text
        }
        else {
            Write-Host $text
        }
    }
    if ($exitCode -ne 0) {
        throw "Runtime bootstrap failed (exit code $exitCode)."
    }
    if (-not $jsonLine) {
        throw "Runtime bootstrap did not return a runtime description."
    }
    return $jsonLine | ConvertFrom-Json
}

function Convert-NativeOutputLine {
    param($Line)
    if ($Line -is [System.Management.Automation.ErrorRecord]) {
        return [string]$Line.ToString()
    }
    return [string]$Line
}

function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [object[]]$ArgumentList = @(),
        [string]$WorkingDirectory,
        [switch]$Quiet
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pushed = $false
    try {
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            Push-Location -LiteralPath $WorkingDirectory
            $pushed = $true
        }
        $output = & $FilePath @ArgumentList 2>&1
        $code = 0
        if ($null -ne $LASTEXITCODE) {
            $code = [int]$LASTEXITCODE
        }
        if (-not $Quiet) {
            foreach ($line in @($output)) {
                $text = Convert-NativeOutputLine -Line $line
                if (-not [string]::IsNullOrWhiteSpace($text) -and $text -notmatch 'RemoteException') {
                    Write-Host $text
                }
            }
        }
        return ,$code
    }
    finally {
        if ($pushed) {
            Pop-Location
        }
        $ErrorActionPreference = $previous
    }
}

function Assert-LastExitCode {
    param(
        [Parameter(Mandatory)][string]$Action,
        $ExitCode = $LASTEXITCODE
    )

    if ($ExitCode -is [System.Array]) {
        $ExitCode = $ExitCode[-1]
    }
    $code = [int]$ExitCode
    if ($code -ne 0) {
        throw "$Action failed (exit code $code)."
    }
}

function Test-PythonPackages {
    param([Parameter(Mandatory)][string]$Python)

    $probe = "import importlib.util, sys; mods=('fastapi','uvicorn','boto3','dotenv','multipart','cryptography'); sys.exit(0 if all(importlib.util.find_spec(name) for name in mods) else 1)"
    return (Invoke-Native -FilePath $Python -ArgumentList @("-c", $probe) -Quiet) -eq 0
}

function Get-ProxyEnvironmentNames {
    @(
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "PIP_PROXY",
        "NPM_CONFIG_PROXY",
        "NPM_CONFIG_HTTPS_PROXY"
    )
}

function Invoke-WithoutProxyEnv {
    param([Parameter(Mandatory)][scriptblock]$Script)

    $saved = New-Object System.Collections.Generic.List[object]
    foreach ($name in Get-ProxyEnvironmentNames) {
        $saved.Add([pscustomobject]@{
            Name = $name
            Value = [Environment]::GetEnvironmentVariable($name, "Process")
        }) | Out-Null
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
    try {
        return & $Script
    }
    finally {
        foreach ($item in $saved) {
            if ($null -ne $item.Value -and $item.Value -ne "") {
                [Environment]::SetEnvironmentVariable($item.Name, $item.Value, "Process")
                Set-Item -Path "Env:$($item.Name)" -Value $item.Value
            }
        }
    }
}

function Get-PythonPackageInstallPlans {
    @(
        [ordered]@{
            name = "current network"
            clearProxy = $false
            arguments = @("-m", "pip", "install", "--disable-pip-version-check", "-r")
        },
        [ordered]@{
            name = "direct connection"
            clearProxy = $true
            arguments = @("-m", "pip", "install", "--disable-pip-version-check", "--proxy", "", "-r")
        },
        [ordered]@{
            name = "Tsinghua PyPI mirror"
            clearProxy = $true
            arguments = @(
                "-m", "pip", "install", "--disable-pip-version-check", "--proxy", "",
                "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                "--trusted-host", "pypi.tuna.tsinghua.edu.cn", "-r"
            )
        },
        [ordered]@{
            name = "Aliyun PyPI mirror"
            clearProxy = $true
            arguments = @(
                "-m", "pip", "install", "--disable-pip-version-check", "--proxy", "",
                "-i", "https://mirrors.aliyun.com/pypi/simple",
                "--trusted-host", "mirrors.aliyun.com", "-r"
            )
        }
    )
}

function Get-NpmInstallPlans {
    param([Parameter(Mandatory)][string]$WebRoot)
    @(
        [ordered]@{
            name = "current network"
            clearProxy = $false
            arguments = @("install")
        },
        [ordered]@{
            name = "direct connection"
            clearProxy = $true
            arguments = @("install")
        },
        [ordered]@{
            name = "npmmirror registry"
            clearProxy = $true
            arguments = @("install", "--registry", "https://registry.npmmirror.com")
        }
    )
}

function Invoke-InstallPlan {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)]$Plan,
        [object[]]$ArgumentList,
        [string]$WorkingDirectory
    )

    Write-Host "Trying $($Plan.name)..."
    if ($Plan.clearProxy) {
        return Invoke-WithoutProxyEnv { Invoke-Native -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory }
    }
    return Invoke-Native -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory
}

function Install-PythonPackages {
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][string]$RequirementsPath
    )

    foreach ($plan in Get-PythonPackageInstallPlans) {
        $arguments = @($plan.arguments) + @($RequirementsPath)
        $code = Invoke-InstallPlan -FilePath $Python -Plan $plan -ArgumentList $arguments
        if ($code -eq 0 -and (Test-PythonPackages -Python $Python)) {
            return
        }
        Write-Host "Python package install via $($plan.name) failed (exit code $code)."
    }

    throw "Python dependency installation failed. pip could not reach a package index, usually because a system HTTP proxy is set but unreachable. Turn off the unused proxy or set a working HTTP_PROXY/HTTPS_PROXY, then run start-aigc.bat again."
}

function Install-NpmPackages {
    param(
        [Parameter(Mandatory)][string]$Npm,
        [Parameter(Mandatory)][string]$WebRoot
    )

    $packageJson = Join-Path $WebRoot "package.json"
    if (-not (Test-Path -LiteralPath $packageJson)) {
        throw "Web application is missing. Expected package.json at $packageJson. Unpack the full AIGC repository so the web folder is next to start-aigc.bat."
    }

    foreach ($plan in Get-NpmInstallPlans -WebRoot $WebRoot) {
        $code = Invoke-InstallPlan -FilePath $Npm -Plan $plan -ArgumentList @($plan.arguments) -WorkingDirectory $WebRoot
        if ($code -eq 0 -and (Test-Path -LiteralPath (Join-Path $WebRoot "node_modules"))) {
            return
        }
        Write-Host "Web dependency install via $($plan.name) failed (exit code $code)."
    }

    throw "Web dependency installation failed in $WebRoot. If npm reported a missing package.json, unpack the full repository so the web folder is present. Otherwise turn off an unused HTTP proxy or set a working HTTP_PROXY/HTTPS_PROXY, then run start-aigc.bat again."
}

if (Test-Path -LiteralPath $portPath) {
    $savedPort = 0
    if ([int]::TryParse((Get-Content -LiteralPath $portPath -Raw).Trim(), [ref]$savedPort) -and $savedPort -gt 0 -and $savedPort -le 65535) {
        Set-StudioEndpoint -Port $savedPort
    }
}

if (-not $Dev -and (Test-AigcStudio)) {
    Write-Host "AIGC Studio is already running at $studioUrl"
    Open-Studio
    exit 0
}

Write-Host "[1/4] Checking Python and Node.js..."
$runtime = Invoke-RuntimeEnsure
$python = [string]$runtime.venvPython
$node = [string]$runtime.node
$npm = [string]$runtime.npm
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $npm)) {
    throw "Runtime bootstrap returned missing Python, Node.js, or npm paths."
}
$env:Path = "$(Split-Path -Parent $node);$(Split-Path -Parent $python);$env:Path"
Write-Host "Using Python $($runtime.pythonVersion) at $python"
Write-Host "Using Node.js $($runtime.nodeMajor) at $node"

Write-Host "[2/4] Checking Python dependencies..."
if (-not (Test-PythonPackages -Python $python)) {
    Write-Host "Installing Python dependencies..."
    Install-PythonPackages -Python $python -RequirementsPath $requirementsPath
}

Write-Host "[3/4] Preparing the web application..."
$packageJson = Join-Path $webRoot "package.json"
if (-not (Test-Path -LiteralPath $packageJson)) {
    throw "Web application is missing. Expected package.json at $packageJson. Unpack the full AIGC repository so the web folder is next to start-aigc.bat."
}
$npmList = Invoke-Native -FilePath $npm -ArgumentList @("ls", "--depth=0") -WorkingDirectory $webRoot -Quiet
if (-not (Test-Path -LiteralPath (Join-Path $webRoot "node_modules")) -or $npmList -ne 0) {
    Write-Host "Installing web dependencies..."
    Install-NpmPackages -Npm $npm -WebRoot $webRoot
}

if (-not $Dev) {
    Assert-LastExitCode -Action "Web application build" -ExitCode (Invoke-Native -FilePath $npm -ArgumentList @("run", "build") -WorkingDirectory $webRoot)
}

Write-Host "[4/4] Starting AIGC Studio..."
if (-not (Test-AigcStudio)) {
    Set-StudioEndpoint -Port (Find-StudioPort)

    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $stdoutLog = Join-Path $runtimeRoot "studio.stdout.log"
    $stderrLog = Join-Path $runtimeRoot "studio.stderr.log"
    $server = Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "api_server:app", "--host", "127.0.0.1", "--port", "$studioPort" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline -and -not (Test-AigcStudio)) {
        if ($server.HasExited) {
            throw "AIGC Studio exited before becoming ready (exit code $($server.ExitCode)). See $stderrLog"
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-AigcStudio)) {
        if (-not $server.HasExited) {
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        }
        throw "AIGC Studio did not become ready within 20 seconds. See $stderrLog"
    }

    Set-Content -LiteralPath (Join-Path $runtimeRoot "studio.pid") -Value $server.Id -Encoding Ascii
    Set-Content -LiteralPath $portPath -Value $studioPort -Encoding Ascii
    Write-Host "AIGC Studio started in the background (PID $($server.Id))."
    Write-Host "To stop it, run: .\stop-aigc.bat"
}
else {
    Write-Host "Reusing the AIGC Studio backend already running on port $studioPort."
}

if ($Dev) {
    Write-Host "Starting the development UI at http://127.0.0.1:5173"
    $env:AIGC_API_PORT = "$studioPort"
    Assert-LastExitCode -Action "Development UI" -ExitCode (Invoke-Native -FilePath $npm -ArgumentList @("run", "dev") -WorkingDirectory $webRoot)
    exit 0
}

Write-Host "AIGC Studio is ready at $studioUrl"
Open-Studio
