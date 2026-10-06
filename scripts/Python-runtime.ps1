function New-AppPythonEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$PythonVersion,
        [string]$Interpreter = ''
    )
    $environmentPython = Join-Path $Destination 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $environmentPython)) {
        if ($Interpreter) {
            if (-not (Test-Path -LiteralPath $Interpreter -PathType Leaf)) {
                throw "Brak interpretera Python: $Interpreter"
            }
            & $Interpreter -m venv $Destination
        }
        else {
            if (-not (Get-Command -Name 'py' -ErrorAction SilentlyContinue)) {
                throw "Zainstaluj Python $PythonVersion z launcherem py albo podaj sciezke do interpretera."
            }
            & py "-$PythonVersion" -m venv $Destination
        }
        if ($LASTEXITCODE -ne 0) { throw "Nie udalo sie utworzyc srodowiska: $Destination" }
    }
    $detectedVersion = & $environmentPython -c 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))'
    if ($LASTEXITCODE -ne 0 -or $detectedVersion -ne $PythonVersion) {
        throw "Srodowisko $Destination wymaga Python $PythonVersion; wykryto $detectedVersion."
    }
}
