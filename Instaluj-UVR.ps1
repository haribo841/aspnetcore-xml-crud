param([string]$Interpreter = '')
$ErrorActionPreference = 'Stop'
$appFolder = $PSScriptRoot
. (Join-Path $appFolder 'scripts\Python-runtime.ps1')
$uvrEnvironment = Join-Path $appFolder '.venv-uvr'
New-AppPythonEnvironment -Destination $uvrEnvironment -PythonVersion '3.12' -Interpreter $Interpreter
$uvrPython = Join-Path $uvrEnvironment 'Scripts\python.exe'
& $uvrPython -m pip install torch==2.11.0+cu126 torchvision==0.26.0+cu126 --index-url https://download.pytorch.org/whl/cu126
if ($LASTEXITCODE -ne 0) { throw 'Instalacja CUDA 12.6 nie powiodla sie.' }
& $uvrPython -m pip install -r (Join-Path $appFolder 'requirements-uvr.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Instalacja UVR nie powiodla sie.' }
Write-Host 'Gotowe. W oknie kolejki kliknij Pobierz model UVR.'
