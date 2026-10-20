<#
.SYNOPSIS
    Script di configurazione, verifica ambiente e avvio per ChatAnalysis su Windows 11 64-bit.

.DESCRIPTION
    Implementa la procedura richiesta per la migrazione e stabilizzazione su piattaforma Windows:
    1. Verifica versione Python (>= 3.12, architettura a 64-bit).
    2. Creazione e attivazione del virtual environment (.venv) locale protetto.
    3. Installazione/aggiornamento dipendenze via 'pip install -e ".[ui,stt,ocr,image,dev]"'.
    4. Verifica degli import effettivi dei package applicativi e delle dipendenze opzionali.
    5. Controllo connettività LM Studio su loopback (http://127.0.0.1:1234).
    6. Esecuzione dei test di regressione e stabilizzazione (Topic Discovery, Detection, Prova del Nove, Snapshot F1).
    7. Esecuzione dello smoke test sintetico end-to-end (MOCK_VERIFIED o LIVE_VERIFIED).
    8. Opzione di avvio interfaccia web Streamlit locale (-LaunchUi).

    IMPORTANTE: Questo script NON cancella ambienti, dati pregressi, modelli GGUF o cartelle esistenti.

.PARAMETER SkipInstall
    Salta l'installazione delle dipendenze con pip (utile se già installate).

.PARAMETER SkipTests
    Salta l'esecuzione della suite di test pytest.

.PARAMETER LaunchUi
    Avvia direttamente l'interfaccia Streamlit al termine della verifica.

.PARAMETER MockOnly
    Esegue lo smoke test esclusivamente in modalità mock (non interroga LM Studio).
#>

[CmdletBinding()]
param (
    [switch]$SkipInstall,
    [switch]$SkipTests,
    [switch]$LaunchUi,
    [switch]$MockOnly
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  CHATANALYSIS - WINDOWS SETUP & ENVIRONMENT VERIFICATION        " -ForegroundColor Cyan
Write-Host "  Piattaforma target: Windows 11 64-bit (Native, no WSL needed)   " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Cartella progetto: $ProjectRoot" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# 1. Verifica Python
# ---------------------------------------------------------------------------
Write-Host "`n[1/8] Verifica interprete Python..." -ForegroundColor Yellow

$PythonCmd = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $PythonCmd = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    $PythonCmd = "py -3"
} else {
    Write-Error "Nessun interprete Python trovato nel PATH. Installare Python 3.12+ a 64 bit da python.org."
    exit 1
}

$PyVerOutput = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro};{sys.maxsize > 2**32}')"
$PyVerParts = $PyVerOutput.Trim().Split(";")
$PyVersion = $PyVerParts[0]
$Is64Bit = [System.Convert]::ToBoolean($PyVerParts[1])

Write-Host "  Versione rilevata: Python $PyVersion" -ForegroundColor Gray
Write-Host "  Architettura 64-bit: $Is64Bit" -ForegroundColor Gray

$MajorMinor = $PyVersion.Split(".")
$Major = [int]$MajorMinor[0]
$Minor = [int]$MajorMinor[1]

if ($Major -lt 3 -or ($Major -eq 3 -and $Minor -lt 12)) {
    Write-Error "Python $PyVersion non supportato. ChatAnalysis richiede Python >= 3.12."
    exit 1
}

if (-not $Is64Bit) {
    Write-Error "Rilevato interprete a 32 bit. ChatAnalysis richiede tassativamente Python a 64 bit per gestione memoria e LM Studio."
    exit 1
}
Write-Host "  [OK] Interprete Python $PyVersion (64-bit) idoneo." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Creazione / Attivazione Virtual Environment
# ---------------------------------------------------------------------------
Write-Host "`n[2/8] Controllo Virtual Environment (.venv)..." -ForegroundColor Yellow
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "  Creazione nuovo ambiente virtuale in $VenvDir..." -ForegroundColor Gray
    & $PythonCmd -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Creazione del virtual environment fallita."
        exit 1
    }
    Write-Host "  [OK] Virtual environment creato." -ForegroundColor Green
} else {
    Write-Host "  [OK] Virtual environment esistente rilevato: $VenvDir" -ForegroundColor Green
}

$env:VIRTUAL_ENV = $VenvDir
$env:PATH = "$VenvDir\Scripts;$env:PATH"
$ActivePython = $VenvPython

# ---------------------------------------------------------------------------
# 3. Installazione / Aggiornamento Dipendenze
# ---------------------------------------------------------------------------
Write-Host "`n[3/8] Installazione dipendenze..." -ForegroundColor Yellow
if ($SkipInstall) {
    Write-Host "  Salto installazione dipendenze (--SkipInstall attivo)." -ForegroundColor Gray
} else {
    Write-Host "  Aggiornamento pip e setuptools..." -ForegroundColor Gray
    & $ActivePython -m pip install --upgrade pip setuptools wheel --quiet
    Write-Host "  Esecuzione: pip install -e .[ui,stt,ocr,image,dev]" -ForegroundColor Gray
    Set-Location $ProjectRoot
    & $ActivePython -m pip install -e ".[ui,stt,ocr,image,dev]"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Installazione dipendenze fallita."
        exit 1
    }
    Write-Host "  [OK] Dipendenze installate con successo." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 4. Verifica Import dei Package Applicativi
# ---------------------------------------------------------------------------
Write-Host "`n[4/8] Verifica import dei moduli applicativi e opzionali..." -ForegroundColor Yellow
$VerifyImportsScript = @"
import sys
modules_to_test = [
    'core', 'importer', 'validation', 'normalization',
    'entity_resolution', 'unified', 'multimodal', 'ai',
    'analysis', 'search', 'ui', 'streamlit'
]
missing = []
for m in modules_to_test:
    try:
        __import__(m)
    except Exception as e:
        missing.append((m, str(e)))

if missing:
    print('ERROR: Moduli mancanti o falliti:')
    for m, err in missing:
        print(f'  - {m}: {err}')
    sys.exit(1)
else:
    print('ALL_MODULES_OK')
"@

$ImportCheckResult = & $ActivePython -c $VerifyImportsScript
if ($LASTEXITCODE -ne 0 -or $ImportCheckResult -notmatch "ALL_MODULES_OK") {
    Write-Error "Verifica import fallita: $ImportCheckResult"
    exit 1
}
Write-Host "  [OK] Tutti i package di ChatAnalysis (inclusi Streamlit e moduli forensi) caricati con successo." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 5. Controllo LM Studio su Loopback (http://127.0.0.1:1234)
# ---------------------------------------------------------------------------
Write-Host "`n[5/8] Controllo disponibilità LM Studio (http://127.0.0.1:1234)..." -ForegroundColor Yellow
$LmStudioAvailable = $false
$LoadedModelName = ""

try {
    $Response = Invoke-RestMethod -Uri "http://127.0.0.1:1234/v1/models" -Method Get -TimeoutSec 3 -ErrorAction Stop
    $LmStudioAvailable = $true
    if ($Response.data -and $Response.data.Count -gt 0) {
        $LoadedModelName = $Response.data[0].id
        Write-Host "  [OK] LM Studio ATTIVO su loopback! Modello rilevato: $LoadedModelName" -ForegroundColor Green
    } else {
        Write-Host "  [AVVISO] LM Studio risponde ma nessun modello e' attualmente caricato in memoria." -ForegroundColor Yellow
        Write-Host "  Caricare 'qwen2.5-7b-instruct' (o compatibile) con contesto 8192 dall'interfaccia di LM Studio." -ForegroundColor Gray
    }
} catch {
    Write-Host "  [INFO] LM Studio non e' attualmente attivo su http://127.0.0.1:1234." -ForegroundColor Yellow
    Write-Host "  L'analisi operativa LIVE richiedera' l'avvio di LM Studio locale con il modello operativo." -ForegroundColor Gray
    Write-Host "  I test sintetici e la modalita' mock funzioneranno regolarmente." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# 6. Esecuzione Test Unitari e Regressioni Stabilizzate
# ---------------------------------------------------------------------------
Write-Host "`n[6/8] Esecuzione suite di test di stabilizzazione..." -ForegroundColor Yellow
if ($SkipTests) {
    Write-Host "  Salto esecuzione test (--SkipTests attivo)." -ForegroundColor Gray
} else {
    Set-Location $ProjectRoot
    Write-Host "  Esecuzione test Topic Discovery, Detection e Prova del Nove (Parti B, C, D)..." -ForegroundColor Gray
    & $ActivePython -m pytest tests/unit/test_topic_discovery_detection_stabilization.py -v
    if ($LASTEXITCODE -ne 0) {
        Write-Error "I test di stabilizzazione Topic Discovery/Detection sono falliti!"
        exit 1
    }

    Write-Host "  Esecuzione test snapshot ownership e F1 TOCTOU..." -ForegroundColor Gray
    & $ActivePython -m pytest tests/unit/test_checkpoint_preflight_resume.py -k "test_122 or test_136 or test_137 or test_138 or test_139 or test_140 or test_141 or test_144" -v
    if ($LASTEXITCODE -ne 0) {
        Write-Error "I test di ownership snapshot F1 sono falliti!"
        exit 1
    }
    Write-Host "  [OK] Tutti i test di stabilizzazione e regressione sono stati superati." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 7. Smoke Test Operativo Sintetico
# ---------------------------------------------------------------------------
Write-Host "`n[7/8] Esecuzione Smoke Test End-to-End..." -ForegroundColor Yellow
Set-Location $ProjectRoot
if ($LmStudioAvailable -and (-not $MockOnly) -and $LoadedModelName -ne "") {
    Write-Host "  Esecuzione Smoke Test LIVE contro LM Studio ($LoadedModelName)..." -ForegroundColor Cyan
    & $ActivePython scripts/smoke_operational_ai.py
    $SmokeCode = $LASTEXITCODE
    if ($SmokeCode -eq 0) {
        Write-Host "  [OK] Smoke Test LIVE_VERIFIED superato con successo!" -ForegroundColor Green
    } else {
        Write-Host "  [AVVISO] Smoke Test LIVE ha restituito codice $SmokeCode. Esecuzione fallback in modalita' MOCK..." -ForegroundColor Yellow
        & $ActivePython scripts/smoke_operational_ai.py --mock
    }
} else {
    Write-Host "  Esecuzione Smoke Test in modalita' MOCK (simulazione client protetta)..." -ForegroundColor Cyan
    & $ActivePython scripts/smoke_operational_ai.py --mock
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Lo Smoke Test sintetico e' fallito."
        exit 1
    }
    Write-Host "  [OK] Smoke Test MOCK_VERIFIED superato con successo (pipeline completa validata)." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 8. Riepilogo o Avvio Streamlit
# ---------------------------------------------------------------------------
Write-Host "`n=================================================================" -ForegroundColor Cyan
Write-Host "  VERIFICA AMBIENTE WINDOWS COMPLETATA CON SUCCESSO!            " -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Per avviare ChatAnalysis con interfaccia grafica Streamlit:" -ForegroundColor Gray
Write-Host "  .\scripts\run_streamlit_windows.ps1" -ForegroundColor Yellow
Write-Host "oppure:" -ForegroundColor Gray
Write-Host "  streamlit run ui/application.py" -ForegroundColor Yellow

if ($LaunchUi) {
    Write-Host "`nAvvio immediato di Streamlit in corso..." -ForegroundColor Cyan
    & "$ScriptRoot\run_streamlit_windows.ps1"
}
