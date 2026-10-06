param(
    [string]$AsrInterpreter = '',
    [string]$DiarizationInterpreter = '',
    [switch]$WithoutSpeakers
)
$ErrorActionPreference = 'Stop'
$appFolder = $PSScriptRoot
. (Join-Path $appFolder 'scripts\Python-runtime.ps1')
$asrEnvironment = Join-Path $appFolder '.venv'
New-AppPythonEnvironment -Destination $asrEnvironment -PythonVersion '3.13' -Interpreter $AsrInterpreter
$asrPython = Join-Path $asrEnvironment 'Scripts\python.exe'
& $asrPython -m pip install -r (Join-Path $appFolder 'requirements-batch.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Instalacja Whisper / yt-dlp nie powiodla sie.' }
if (-not $WithoutSpeakers) {
    $diarEnvironment = Join-Path $appFolder '.venv-diarization'
    New-AppPythonEnvironment -Destination $diarEnvironment -PythonVersion '3.12' -Interpreter $DiarizationInterpreter
    $diarPython = Join-Path $diarEnvironment 'Scripts\python.exe'
    & $diarPython -m pip install torch==2.11.0+cu126 torchaudio==2.11.0+cu126 --index-url https://download.pytorch.org/whl/cu126
    if ($LASTEXITCODE -ne 0) { throw 'Instalacja CUDA 12.6 nie powiodla sie.' }
    & $diarPython -m pip install -r (Join-Path $appFolder 'requirements-diarization.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Instalacja pyannote nie powiodla sie.' }
}
foreach ($dependency in @('ffmpeg', 'ffprobe', 'node')) {
    if (-not (Get-Command -Name $dependency -ErrorAction SilentlyContinue)) {
        Write-Warning "Dodaj $dependency do PATH albo wskaz pelna sciezke w ustawienia.json."
    }
}
Write-Host 'Srodowiska gotowe. Pobierz Whisper: .\.venv\Scripts\python.exe transcribe.py download'
Write-Host 'Nastepnie otworz Uruchom-kolejke.vbs. Skonfiguruj mowcow i wybierz wlasna probke do 2 minut.'
if ($WithoutSpeakers) {
    Write-Host 'Pominieto srodowisko mowcow. W ustawienia.json ustaw diarization na false przed Start.'
}
