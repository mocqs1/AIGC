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
$studioPort = 8000
$studioUrl = ""
$healthUrl = ""
$openApiUrl = ""

function Set-StudioEndpoint {
    param([Parameter(Mandatory)][int]$Port)
    $script:studioPort = $Port
    $script:studioUrl = "http://127.0.0.1:$Port"
    $script:healthUrl = "$script:studioUrl/api/health"
    $script:openApiUrl = "$script:studioUrl/openapi.json"
}

Set-StudioEndpoint -Port $studioPort

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

function Find-StudioPort {
    foreach ($candidate in 8000..8099) {
        if (-not (Test-PortInUse -Port $candidate)) {
            return $candidate
        }
    }
    throw "No available local port was found between 8000 and 8099."
}

function Test-AigcStudio {
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


function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Action)

    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed (exit code $LASTEXITCODE)."
    }
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
& $python -c "import fastapi, uvicorn, boto3, dotenv, multipart, cryptography" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python dependencies..."
    & $python -m pip install --upgrade pip
    Assert-LastExitCode -Action "pip upgrade"
    & $python -m pip install -r $requirementsPath
    Assert-LastExitCode -Action "Python dependency installation"

    & $python -c "import fastapi, uvicorn, boto3, dotenv, multipart, cryptography" 2>$null
    Assert-LastExitCode -Action "Python dependency verification"
}

Write-Host "[3/4] Preparing the web application..."
& $npm ls --prefix $webRoot --depth=0 2>$null | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $webRoot "node_modules")) -or $LASTEXITCODE -ne 0) {
    Write-Host "Installing web dependencies..."
    & $npm install --prefix $webRoot
    Assert-LastExitCode -Action "Web dependency installation"
}

if (-not $Dev) {
    & $npm run build --prefix $webRoot
    Assert-LastExitCode -Action "Web application build"
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
    & $npm run dev --prefix $webRoot
    Assert-LastExitCode -Action "Development UI"
    exit 0
}

Write-Host "AIGC Studio is ready at $studioUrl"
Open-Studio
