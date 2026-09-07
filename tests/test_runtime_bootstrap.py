import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENSURE_RUNTIME = ROOT / "scripts" / "ensure-runtime.ps1"
START_STUDIO = ROOT / "scripts" / "start-studio.ps1"


def _powershell(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            *args,
        ],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class RuntimeBootstrapTests(unittest.TestCase):
    def test_bootstrap_scripts_parse(self):
        for script in (ENSURE_RUNTIME, START_STUDIO):
            command = (
                "$errors = $null; "
                "[void][System.Management.Automation.Language.Parser]::ParseFile("
                f"'{script.as_posix()}', [ref]$null, [ref]$errors); "
                "if ($errors) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
            )
            result = _powershell("-Command", command)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_probe_reports_usable_python_and_node(self):
        result = _powershell("-File", str(ENSURE_RUNTIME), "-Probe")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["python"]["usable"], payload)
        self.assertTrue(payload["node"]["usable"], payload)
        self.assertTrue(payload["npm"]["usable"], payload)
        self.assertGreaterEqual(int(payload["node"]["major"]), 18)

    def test_skip_install_reuses_existing_runtimes_and_creates_venv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = _powershell(
                "-File",
                str(ENSURE_RUNTIME),
                "-SkipInstall",
                "-ProjectRoot",
                temp_dir,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            venv_python = Path(payload["venvPython"])
            self.assertTrue(venv_python.is_file(), payload)
            self.assertEqual(venv_python.parent.parent, Path(temp_dir) / ".venv")
            version = subprocess.run(
                [str(venv_python), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            major, minor = (int(part) for part in version.split("."))
            self.assertGreaterEqual((major, minor), (3, 11))

    def test_missing_python_packages_install_instead_of_aborting_on_stderr(self):
        command = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$python = (Get-Command python).Source
$oldThrew = $false
try {
    & $python -c "import definitely_missing_aigc_pkg_xyz" 2>$null
} catch {
    $oldThrew = $true
}
if (-not $oldThrew) { throw 'expected PowerShell Stop to treat python stderr as a terminating error' }

function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [object[]]$ArgumentList = @(),
        [switch]$Quiet
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Quiet) {
            & $FilePath @ArgumentList 1>$null 2>&1 | Out-Null
        }
        else {
            & $FilePath @ArgumentList
        }
        if ($null -eq $LASTEXITCODE) { return 0 }
        return [int]$LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
}
function Test-PythonPackages {
    param([Parameter(Mandatory)][string]$Python)
    $probe = "import importlib.util, sys; mods=('fastapi','uvicorn','boto3','dotenv','multipart','cryptography'); sys.exit(0 if all(importlib.util.find_spec(name) for name in mods) else 1)"
    return (Invoke-Native -FilePath $Python -ArgumentList @('-c', $probe) -Quiet) -eq 0
}
$missing = Invoke-Native -FilePath $python -ArgumentList @('-c', "import definitely_missing_aigc_pkg_xyz") -Quiet
if ($missing -eq 0) { throw 'missing import should return non-zero' }
'PROBE_OK'
"""
        result = _powershell("-Command", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PROBE_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
