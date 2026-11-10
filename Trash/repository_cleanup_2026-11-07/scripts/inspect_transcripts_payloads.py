#!/usr/bin/env python3
"""
scripts/inspect_transcripts_payloads.py
---------------------------------------
Verifica rigorosa della presenza di payload completi, diagnostiche e dati dei chunk
relativi a run_disc_3f3731eb04 e run_disc_8db1424645 all'interno dei log e dei file di sessione.
"""
import json
from pathlib import Path
import sys

def main():
    transcript_path = Path(r"C:\Users\Luca\.gemini\antigravity-ide\brain\d14e319d-796f-43c8-9ab2-99229890a270\.system_generated\logs\transcript_full.jsonl")
    if not transcript_path.exists():
        print(f"File non trovato: {transcript_path}")
        return

    print(f"Analisi transcript: {transcript_path}")
    print(f"Dimensione: {transcript_path.stat().st_size} bytes")

    matches = []
    line_num = 0
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line_num += 1
            if "run_disc_3f3731eb04" in line:
                matches.append((line_num, len(line)))
                print(f"Match linea {line_num}: lunghezza {len(line)}")

    print(f"\nTotale linee contenenti run_disc_3f3731eb04: {len(matches)}")

    # Ispeziona in dettaglio ciascuna linea corrispondente
    with open(transcript_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            if idx in [m[0] for m in matches]:
                print("=" * 80)
                print(f"DETTAGLIO LINEA {idx}:")
                try:
                    obj = json.loads(line)
                    print(f"  Keys: {list(obj.keys())}")
                    print(f"  Type: {obj.get('type')}, Source: {obj.get('source')}, Step: {obj.get('step_index')}")
                    content = str(obj.get("content", ""))
                    tool_calls = obj.get("tool_calls", [])
                    print(f"  Content length: {len(content)}, tool_calls count: {len(tool_calls)}")
                    
                    # Controlla se ci sono payload completi con "topics", "label", "short_description", "evidence_ids"
                    has_topics = "topics" in line
                    has_checkpoint_chunks = "checkpoint_chunks" in line
                    has_evidence_ids = "evidence_ids" in line
                    print(f"  Contiene 'topics': {has_topics}")
                    print(f"  Contiene 'checkpoint_chunks': {has_checkpoint_chunks}")
                    print(f"  Contiene 'evidence_ids': {has_evidence_ids}")

                    # Campione del contenuto
                    if content:
                        print(f"  Content snippet (primi 500 char):\n{content[:500]}")
                    if tool_calls:
                        print(f"  Tool calls sample: {json.dumps(tool_calls)[:500]}")

                except Exception as e:
                    print(f"  Errore parsing JSON: {e}")
                    print(f"  Raw snippet: {line[:300]}")

if __name__ == "__main__":
    main()
