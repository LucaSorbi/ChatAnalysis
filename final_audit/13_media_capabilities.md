# Stato e Gestione delle Capacità Multimodali Reali (Fase 33, 34, 35)

## 1. Dichiarazione Trasparente dello Stato Reale
- Durante l'ingestion di file reali (ZIP o TXT WhatsApp, Cellebrite), la pipeline genera `MessageEvidenceBundle` contenenti esclusivamente sezioni di evidenza `ORIGINAL_TEXT`.
- **Trasparenza**: Non viene eseguita alcuna estrazione automatica di Speech-To-Text (STT), OCR o Vision durante l'ingestion base di un file reale.
- Il dataset sintetico DEMO include sezioni multimodali simulate (es. `STT_TRANSCRIPTION`, `OCR_TEXT`, `VISION_DESCRIPTION`) ad uso esclusivo del collaudo dell'interfaccia e dei filtri visuali.

## 2. Elaborazione Media Offline Esplicita
Per analizzare allegati media reali (audio vocali o immagini):
1. Gli allegati estratti da archivi ZIP transitano tramite un layer di sandbox protetto (`_validate_zip_archive`), con verifica di hash CRC/SHA-256 e protezione da path traversal.
2. L'elaborazione deve essere invocata come azione offline separata:
   - **Audio**: Tramite Faster-Whisper reale locale;
   - **OCR**: Tramite Tesseract OCR locale;
   - **Vision**: Tramite modello Vision locale configurato; se assente, la UI dichiara onestamente "Vision locale non configurata".
