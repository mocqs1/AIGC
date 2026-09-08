import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENSURE_RUNTIME = ROOT / "scripts" / "ensure-runtime.ps1"
START_STUDIO = ROOT / "scripts" / "start-studio.ps1"


def _extract_ps_function(source: str, name: str) -> str:
    marker = f"function {name} {{"
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing PowerShell function {name}")
    brace = source.find("{", start)
    depth = 0
    for index, char in enumerate(source[brace:], start=brace):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unclosed PowerShell function {name}")


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

    def test_native_exit_code_ignores_command_stdout(self):
        source = START_STUDIO.read_text(encoding="utf-8")
        convert_line = _extract_ps_function(source, "Convert-NativeOutputLine")
        invoke_native = _extract_ps_function(source, "Invoke-Native")
        assert_exit = _extract_ps_function(source, "Assert-LastExitCode")
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-StrictMode -Version Latest\n"
            f"{convert_line}\n"
            f"{invoke_native}\n"
            f"{assert_exit}\n"
            "$code = Invoke-Native -FilePath $env:ComSpec -ArgumentList @('/c', 'echo pip-like line 1&echo pip-like line 2')\n"
            "if ($code -is [System.Array]) { throw ('exit code leaked as array: ' + ($code | ConvertTo-Json -Compress)) }\n"
            "Assert-LastExitCode -Action 'pip upgrade' -ExitCode $code\n"
            "Assert-LastExitCode -Action 'pip upgrade nested' -ExitCode (Invoke-Native -FilePath $env:ComSpec -ArgumentList @('/c', 'echo nested-pip-line'))\n"
            "'NATIVE_OK ' + $code\n"
        )
        result = _powershell("-Command", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NATIVE_OK 0", result.stdout)
        self.assertIn("pip-like line 1", result.stdout)
        self.assertIn("pip-like line 2", result.stdout)
        self.assertIn("nested-pip-line", result.stdout)

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
'PROBE_OK'
"""
        result = _powershell("-Command", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PROBE_OK", result.stdout)

    def test_python_install_plans_include_direct_and_mirror_fallbacks(self):
        source = START_STUDIO.read_text(encoding="utf-8")
        get_plans = _extract_ps_function(source, "Get-PythonPackageInstallPlans")
        get_npm = _extract_ps_function(source, "Get-NpmInstallPlans")
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-StrictMode -Version Latest\n"
            f"{get_plans}\n"
            f"{get_npm}\n"
            "$plans = @(Get-PythonPackageInstallPlans)\n"
            "if ($plans.Count -lt 3) { throw 'expected multiple pip fallbacks' }\n"
            "$names = @($plans | ForEach-Object { $_.name })\n"
            "if ($names -notcontains 'direct connection') { throw 'missing direct pip fallback' }\n"
            "if ($names -notcontains 'Tsinghua PyPI mirror') { throw 'missing Tsinghua fallback' }\n"
            "$direct = $plans | Where-Object { $_.name -eq 'direct connection' }\n"
            "if (-not $direct.clearProxy) { throw 'direct pip plan must clear proxy env' }\n"
            "if (@($direct.arguments) -notcontains '--proxy') { throw 'direct pip plan must pass empty --proxy' }\n"
            "$npmPlans = @(Get-NpmInstallPlans -WebRoot 'C:\\temp\\web')\n"
            "$npmNames = @($npmPlans | ForEach-Object { $_.name })\n"
            "if ($npmNames -notcontains 'npmmirror registry') { throw 'missing npm mirror fallback' }\n"
            "foreach ($plan in $npmPlans) {\n"
            "    if (@($plan.arguments) -contains '--prefix') { throw 'npm plans must not use --prefix' }\n"
            "}\n"
            "if (@($npmPlans[0].arguments) -notcontains 'install') { throw 'npm plans must run install in the web folder' }\n"
            "'PLANS_OK'\n"
        )
        result = _powershell("-Command", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PLANS_OK", result.stdout)

    def test_without_proxy_env_restores_original_values(self):
        source = START_STUDIO.read_text(encoding="utf-8")
        get_names = _extract_ps_function(source, "Get-ProxyEnvironmentNames")
        without_proxy = _extract_ps_function(source, "Invoke-WithoutProxyEnv")
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-StrictMode -Version Latest\n"
            f"{get_names}\n"
            f"{without_proxy}\n"
            "$env:HTTP_PROXY = 'http://127.0.0.1:8'\n"
            "$env:HTTPS_PROXY = 'http://127.0.0.1:9'\n"
            "$inside = Invoke-WithoutProxyEnv { [ordered]@{ http = [string]$env:HTTP_PROXY; https = [string]$env:HTTPS_PROXY } }\n"
            "if (-not [string]::IsNullOrWhiteSpace([string]$inside.https)) { throw ('https proxy still visible inside: ' + $inside.https) }\n"
            "if (-not [string]::IsNullOrWhiteSpace([string]$inside.http)) { throw ('http proxy still visible inside: ' + $inside.http) }\n"
            "if ($env:HTTPS_PROXY -ne 'http://127.0.0.1:9') { throw ('https proxy was not restored: ' + $env:HTTPS_PROXY) }\n"
            "if ($env:HTTP_PROXY -ne 'http://127.0.0.1:8') { throw ('http proxy was not restored: ' + $env:HTTP_PROXY) }\n"
            "'PROXY_OK'\n"
        )
        result = _powershell("-Command", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PROXY_OK", result.stdout)

    def test_native_working_directory_supports_spaces_and_parentheses(self):
        source = START_STUDIO.read_text(encoding="utf-8")
        convert_line = _extract_ps_function(source, "Convert-NativeOutputLine")
        invoke_native = _extract_ps_function(source, "Invoke-Native")
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-StrictMode -Version Latest\n"
            f"{convert_line}\n"
            f"{invoke_native}\n"
            "$web = Join-Path $env:TEMP ('AIGC Native (' + [guid]::NewGuid().ToString('N').Substring(0,8) + ')')\n"
            "New-Item -ItemType Directory -Path $web | Out-Null\n"
            "Set-Content -LiteralPath (Join-Path $web 'marker.txt') -Value 'cwd-ok'\n"
            "$before = (Get-Location).Path\n"
            "try {\n"
            "    $code = Invoke-Native -FilePath $env:ComSpec -ArgumentList @('/c', 'type marker.txt & cd') -WorkingDirectory $web\n"
            "    if ($code -ne 0) { throw ('expected success, got ' + $code) }\n"
            "}\n"
            "finally {\n"
            "    Remove-Item -LiteralPath $web -Recurse -Force\n"
            "}\n"
            "if ((Get-Location).Path -ne $before) { throw 'working directory leaked after Invoke-Native' }\n"
            "'CWD_OK'\n"
        )
        result = _powershell("-Command", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CWD_OK", result.stdout)
        self.assertIn("cwd-ok", result.stdout)

    def test_npm_install_fails_fast_when_package_json_missing(self):
        source = START_STUDIO.read_text(encoding="utf-8")
        convert_line = _extract_ps_function(source, "Convert-NativeOutputLine")
        invoke_native = _extract_ps_function(source, "Invoke-Native")
        get_names = _extract_ps_function(source, "Get-ProxyEnvironmentNames")
        without_proxy = _extract_ps_function(source, "Invoke-WithoutProxyEnv")
        get_npm = _extract_ps_function(source, "Get-NpmInstallPlans")
        invoke_plan = _extract_ps_function(source, "Invoke-InstallPlan")
        install_npm = _extract_ps_function(source, "Install-NpmPackages")
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-StrictMode -Version Latest\n"
            f"{convert_line}\n"
            f"{invoke_native}\n"
            f"{get_names}\n"
            f"{without_proxy}\n"
            f"{get_npm}\n"
            f"{invoke_plan}\n"
            f"{install_npm}\n"
            "$web = Join-Path $env:TEMP ('AIGC Missing Web (' + [guid]::NewGuid().ToString('N').Substring(0,8) + ')')\n"
            "New-Item -ItemType Directory -Path $web | Out-Null\n"
            "try {\n"
            "    $threw = $false\n"
            "    try {\n"
            "        Install-NpmPackages -Npm $env:ComSpec -WebRoot $web\n"
            "    } catch {\n"
            "        $threw = $true\n"
            "        if ($_.Exception.Message -notmatch 'package.json') { throw ('unexpected error: ' + $_.Exception.Message) }\n"
            "        if ($_.Exception.Message -notmatch [regex]::Escape($web)) { throw ('error did not name web root: ' + $_.Exception.Message) }\n"
            "    }\n"
            "    if (-not $threw) { throw 'expected missing package.json to fail fast' }\n"
            "}\n"
            "finally {\n"
            "    Remove-Item -LiteralPath $web -Recurse -Force\n"
            "}\n"
            "'MISSING_PKG_OK'\n"
        )
        result = _powershell("-Command", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MISSING_PKG_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
