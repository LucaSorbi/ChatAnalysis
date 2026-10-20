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

# Attiva virtual environment se presente
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvStreamlit = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"

$StreamlitCmd = "streamlit"
if (Test-Path $VenvStreamlit) {
    $StreamlitCmd = $VenvStreamlit
    $env:VIRTUAL_ENV = Join-Path $ProjectRoot ".venv"
    $env:PATH = "$ProjectRoot\.venv\Scripts;$env:PATH"
    Write-Host "Utilizzo ambiente virtuale: $ProjectRoot\.venv" -ForegroundColor Gray
}

# Configurazione ambiente forense locale
$env:PYTHONUTF8 = "1"
$env:STREAMLIT_SERVER_PORT = $Port
$env:STREAMLIT_SERVER_HEADLESS = if ($Browser) { "false" } else { "true" }

Set-Location $ProjectRoot
Write-Host "Interfaccia disponibile su: http://localhost:$Port" -ForegroundColor Green
Write-Host "Premere CTRL+C per arrestare il server.`n" -ForegroundColor Gray

& $StreamlitCmd run ui/application.py --server.port $Port
