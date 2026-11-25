# Prepares a private Python for this program on a PC that has nothing installed.
# Called by start.bat only when no usable Python (3.11+) is found.
#
# - Downloads uv (pinned version, SHA-256 checked) into .tools\uv
# - uv downloads Python 3.12 into .tools\python and creates .venv
# - Everything stays inside the program folder: no admin rights, no system-wide install, no PATH change.
#   Deleting the .tools and .venv folders removes it completely.
#
# Exit codes: 0 ok, 2 uv download failed, 3 checksum mismatch, 4 Python/venv creation failed
param([Parameter(Mandatory = $true)][string]$Root)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is very slow with the progress bar on PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$UvVersion = '0.13.7'
$UvSha256 = '0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021'
$PythonVersion = '3.12'

$tools = Join-Path $Root '.tools'
$uvDir = Join-Path $tools 'uv'
$uv = Join-Path $uvDir 'uv.exe'

if (-not (Test-Path $uv)) {
    $name = 'uv-x86_64-pc-windows-msvc.zip'
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$name"
    $zip = Join-Path $tools $name
    New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
    Write-Host "  - downloading uv $UvVersion ..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    } catch {
        Write-Host "  ! download failed: $($_.Exception.Message)"
        exit 2
    }
    $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash
    if ($actual -ne $UvSha256) {
        Remove-Item -Force $zip
        Write-Host "  ! checksum mismatch (expected $UvSha256, got $actual)"
        exit 3
    }
    Expand-Archive -Path $zip -DestinationPath $uvDir -Force
    Remove-Item -Force $zip
}

# Keep the downloaded Python and the package cache inside the program folder as well
$env:UV_PYTHON_INSTALL_DIR = Join-Path $tools 'python'
$env:UV_CACHE_DIR = Join-Path $tools 'cache'

Write-Host "  - downloading Python $PythonVersion and creating .venv ..."
& $uv venv (Join-Path $Root '.venv') --python $PythonVersion --managed-python --seed
if ($LASTEXITCODE -ne 0) { exit 4 }
exit 0
