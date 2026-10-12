# Stato Operativo LM Studio Locale (Fase 40, 56)

## 1. Ambiente Corrente (Windows Build / Test Host)
- **Stato Esecuzione Live**: `LIVE LM STUDIO: NOT EXECUTED`
- **Diagnostica Loopback**: Il server LM Studio locale sulla porta standard 1234 non è attivo nell'ambiente di test automatizzato.
- **Politica di Veridicità**: Non è stata inventata alcuna esecuzione con Qwen PRESENT. Il test è onestamente marcato NOT EXECUTED.

### Output di Prova Connessione Reale:
```
======================================================================
SMOKE TEST OPERATIVO AI (LM STUDIO / CHATANALYSIS RELEASE GATE)
======================================================================
[MODE] Connessione a LM Studio locale su http://127.0.0.1:1234 (Richiesto: qwen2.5-7b-instruct)

LIVE LM STUDIO: NOT EXECUTED
Motivo: server LM Studio non raggiungibile su http://127.0.0.1:1234.
Prerequisiti per l'ambiente live (PC Ubuntu):
  1. Avviare LM Studio.
  2. Attivare il Server Locale (porta 1234, loopback 127.0.0.1).
  3. Verificare installazione del modello canonico 'qwen2.5-7b-instruct'.
```

---

## 2. Verifica dello Script Operativo con Fake Client Deterministico
Lo script `scripts/smoke_operational_ai.py` è conforme al contratto finale e produce l'output strutturato richiesto dal Release Gate:
```
======================================================================
SMOKE TEST OPERATIVO AI (LM STUDIO / CHATANALYSIS RELEASE GATE)
======================================================================
[MODE] Utilizzo FakeSmokeClient deterministico per diagnostica ambiente CI/Mock.

--- 1. ESECUZIONE TOPIC DISCOVERY LIVE DEMO ---
Status Discovery : COMPLETE
Topic scoperti   : 1

--- 2. PROVA DEL NOVE: SECONDA DETECTION LIVE INDIPENDENTE ---

--- 3. TEST CONTROLLO DIAGNOSTICO: 'Logistica e Ispezione Depositi' ---

======================================================================
REPORT STRUTTURATO RELEASE GATE AI:
======================================================================
requested canonical model : qwen2.5-7b-instruct
resolved model key        : mock-qwen2.5-7b-instruct
instance id               : mock-instance-01
response model            : qwen2.5-7b-instruct
context length            : 8192
discovery topics          : ['Ispezione Depositi e Merci']
discovery evidence IDs    : ['msg::5::VISION_DESCRIPTION']
detection decision        : PRESENT
detection evidence IDs    : ['msg::5::VISION_DESCRIPTION']
consistency               : CONSISTENT
consistency overlap IDs   : ['msg::5::VISION_DESCRIPTION']
latencies                 : Discovery=0.00s, Detection=0.00s, Control=0.00s
======================================================================
ESITO SMOKE TEST: SUCCESSO
```

---

## 3. Istruzioni per l'Esecuzione Finale sul PC Ubuntu
Sul PC Ubuntu target dotato di GPU e server LM Studio 0.4.x:
1. Avviare LM Studio ed abilitare il Server Locale su `127.0.0.1:1234`;
2. Caricare il modello canonico `qwen2.5-7b-instruct` (Q4_K_M) con context length 8192;
3. Eseguire:
   ```bash
   python scripts/smoke_operational_ai.py
   ```
4. Verificare che l'output riporti `ESITO SMOKE TEST: SUCCESSO` e latenze congrue prima di sbloccare i benchmark finali.
