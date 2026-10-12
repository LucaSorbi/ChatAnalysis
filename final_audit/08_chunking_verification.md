# Verifica del Sottosistema di Chunking e Aggregazione (Fase 8, 9, 10, 11, 12)

## 1. Suddivisione Conservativa del Documento
- `split_document_into_chunks()` rispetta l'atomicità dei bundle di evidenza (`MessageEvidenceBundle`).
- Budget deterministico di caratteri (`max_characters`, default 8000) e numero bundle (`max_bundles`, default 25).
- Se una singola sezione o un singolo bundle atomico supera il limite, viene sollevata `AnalysisInputTooLargeError`.

## 2. Normalizzazione Deterministica delle Label Tematiche
- Funzione `normalize_topic_label(label: str)`:
  1. Normalizzazione Unicode NFKC;
  2. Casefolding (`.casefold()`);
  3. Punteggiatura sostituita da spazi (str.maketrans con string.punctuation);
  4. Collasso spazi multipli (`" ".join(norm.split())`).
- Garantisce che variazioni tipografiche come `"Contratti & Merci!"` e `"contratti merci"` vengano riconosciute come identiche senza introdurre dipendenze semantiche fragili.

## 3. Deterministic Support Ranking per `max_topics`
Quando il numero di topic consolidati supera `max_topics`, `aggregate_topic_discovery()` ordina gli elementi secondo la seguente tupla deterministica:
1. `-len(supporting_chunks)`: Numero di chunk indipendenti che supportano il topic (maggior consenso prima);
2. `-len(evidence_ids)`: Numero totale di evidenze uniche a supporto;
3. `first_seen_index`: Prima apparizione cronologica nel flusso documentale;
4. `norm_key`: Tie-breaker alfabetico stabile sulla label normalizzata.

Metadati prodotti:
- `raw_topic_count`: Numero totale di topic estratti dai chunk;
- `consolidated_topic_count`: Numero di topic unici post-normalizzazione;
- `selected_topic_count`: Numero finale di topic restituiti;
- `reduction_strategy`: `"none"` o `"deterministic_support_ranking"`.

## 4. Accounting Esaustivo e Gestione Errori
- Ogni chunk produce un `DiscoveryChunkOutcome` con tracking di `status` (`SUCCESS` o `FAILED`), `error_type` e `safe_error_code`.
- Se 0 chunk hanno successo: solleva `OperationalAnalysisFailedError`.
- Se alcuni chunk riescono e altri falliscono: marcato `status = "PARTIAL"` con `completed_chunk_count`, `failed_chunk_count` ed `expected_chunk_count`.
- Errori di backend fatali (`LmStudioUnavailableError`, `AiModelMismatchError`, ecc.) interrompono immediatamente il ciclo senza retries inutili.
