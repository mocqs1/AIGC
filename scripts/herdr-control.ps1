param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$shared = Join-Path $projectRoot "..\agent_bus\coordination\herdr-control.ps1"
& $shared -ProjectRoot $projectRoot @args
$exitCode = if (Get-Variable LASTEXITCODE -ErrorAction SilentlyContinue) { [int]$LASTEXITCODE } else { 0 }
exit $exitCode
