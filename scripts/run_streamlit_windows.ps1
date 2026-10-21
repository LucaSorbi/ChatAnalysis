<#
.SYNOPSIS
    Avvia l'applicazione Streamlit di ChatAnalysis su Windows 11 nativo.

.DESCRIPTION
    Attiva il virtual environment locale (.venv se presente), imposta le variabili
    di ambiente necessarie (PYTHONUTF8, porta locale) e avvia Streamlit su http://localhost:8501.
#>

[CmdletBinding()]
param (
    [int]$Port = 8501,
    [switch]$Browser
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot

Write-Host "Avvio ChatAnalysis Streamlit su Windows..." -ForegroundColor Cyan

# Attiva virtual environment se presente o seleziona interprete Python
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvStreamlit = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"

$PythonExe = $null
$PythonArgs = @()

if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
    $env:VIRTUAL_ENV = Join-Path $ProjectRoot ".venv"
    $env:PATH = "$ProjectRoot\.venv\Scripts;$env:PATH"
    Write-Host "Utilizzo ambiente virtuale: $ProjectRoot\.venv" -ForegroundColor Gray
} elseif (Get-Command "python" -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
    $PythonArgs = @("-3")
} else {
    Write-Error "Nessun interprete Python trovato nel virtualenv o nel PATH."
    exit 1
}

# Configurazione ambiente forense locale
$env:PYTHONUTF8 = "1"
$env:STREAMLIT_SERVER_PORT = $Port
$env:STREAMLIT_SERVER_HEADLESS = if ($Browser) { "false" } else { "true" }

Set-Location $ProjectRoot
Write-Host "Interfaccia disponibile su: http://localhost:$Port" -ForegroundColor Green
Write-Host "Premere CTRL+C per arrestare il server.`n" -ForegroundColor Gray

if (Test-Path $VenvStreamlit) {
    & $VenvStreamlit run app.py --server.port $Port
} else {
    & $PythonExe @PythonArgs -m streamlit run app.py --server.port $Port
}

