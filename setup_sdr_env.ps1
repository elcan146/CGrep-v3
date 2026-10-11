#Requires -Version 5.1
<#
.SYNOPSIS
    One-shot Python environment setup for all SDR scripts in this repo.

.DESCRIPTION
    Creates a Python 3.11 venv and installs the Python packages needed for
    RTL-SDR, SDRplay (pysdrplay), SoapySDR, and SDRconnect / nRSP-ST backends.
    Hardware drivers (SDRplay API, librtlsdr, SoapySDR runtime) must still be
    installed manually; this script only handles the Python side.
#>
param(
    [string]$VenvName = "py311"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$venvPath = Join-Path $repoRoot $VenvName

# Pick a Python interpreter (prefer the Windows launcher if available)
$pythonExe = if (Get-Command "py" -ErrorAction SilentlyContinue) { "py" } else { "python" }

if (-not (Get-Command $pythonExe -ErrorAction SilentlyContinue)) {
    throw "Python not found in PATH. Install Python 3.11 from https://www.python.org and check 'Add Python to PATH'."
}

# Create venv
if (-not (Test-Path $venvPath)) {
    Write-Host "[*] Creating venv at $venvPath ..."
    if ($pythonExe -eq "py") {
        & $pythonExe "-3.11" "-m" "venv" $venvPath
    } else {
        & $pythonExe "-m" "venv" $venvPath
    }
}

$py = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "venv python not found at $py"
}

# Upgrade pip and build tools
Write-Host "[*] Upgrading pip, setuptools, wheel, hatchling ..."
& $py -m pip install --upgrade pip setuptools wheel hatchling

# Python packages for every supported SDR backend
Write-Host "[*] Installing Python packages for all SDR backends ..."
& $py -m pip install "numpy>=1.24.0" "sqlalchemy>=2.0" "psycopg2-binary>=2.9" "websockets>=13.0" "pyrtlsdr==0.2.93"

# Install the local pysdrplay wrapper
$pysdrplayPath = Join-Path $repoRoot "pysdrplay_tmp"
if (Test-Path $pysdrplayPath) {
    Write-Host "[*] Installing local pysdrplay from $pysdrplayPath ..."
    & $py -m pip install $pysdrplayPath
} else {
    Write-Warning "pysdrplay_tmp not found; skipping"
}

# Verify the Python side works
Write-Host "[*] Verifying installed packages ..."
& $py -c "import numpy, sqlalchemy, psycopg2, rtlsdr, websockets; print('  core packages OK')"

if (Test-Path $pysdrplayPath) {
    & $py -c "from pysdrplay import SDRPlayRadio; print('  pysdrplay OK')"
}

# Check for the optional SDRplay API Windows service
$apiService = Get-Service "SDRplay API Service" -ErrorAction SilentlyContinue
if ($apiService -and $apiService.Status -eq "Running") {
    Write-Host "[*] SDRplay API service is running" -ForegroundColor Green
} else {
    Write-Warning "SDRplay API service not found or not running. Install SDRplay API 3.15."
}

# Check for an RTL-SDR DLL placed in the repo
$librtlsdr = Join-Path $repoRoot "librtlsdr.dll"
if (Test-Path $librtlsdr) {
    Write-Host "[*] librtlsdr.dll found in repo" -ForegroundColor Green
} else {
    Write-Warning "librtlsdr.dll not found in $repoRoot. Place it there for RTL-SDR."
}

Write-Host ""
Write-Host "=== Hardware drivers / runtime still required manually ===" -ForegroundColor Cyan
Write-Host "RTL-SDR:    librtlsdr.dll + rtl-sdr USB driver"
Write-Host "            https://github.com/librtlsdr/librtlsdr/releases"
Write-Host "SDRplay:    SDRplay API 3.15"
Write-Host "            https://www.sdrplay.com/api/"
Write-Host "SoapySDR:   PothosSDR installer or SoapySDR build with Python bindings"
Write-Host "            https://github.com/pothosware/PothosSDR/wiki"
Write-Host "SDRconnect: nRSP-ST / SDRconnect host running at SDRCONNECT_HOST:SDRCONNECT_PORT"
Write-Host ""
Write-Host "Done. Activate the venv with:" -ForegroundColor Green
Write-Host "  .\$VenvName\Scripts\Activate.ps1"
