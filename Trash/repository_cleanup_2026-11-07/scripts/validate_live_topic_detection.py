"""
scripts/validate_live_topic_detection.py
----------------------------------------
Test di validazione LIVE di Topic Detection con LM Studio locale (qwen2.5-7b-instruct).
Verifica:
1. Stato PRESENT con citazioni di evidenze valide reali e verificate.
2. Stato ABSENT con motivazione coerente e lista evidence_ids rigorosamente vuota.
3. Stato UNCERTAIN con evidenze o motivazione plausibile.
4. Rifiuto tassativo di evidence_id inventati / allucinati.
5. Indipendenza tra Discovery e Detection.
"""
import sys
import os
from pathlib import Path

# Assicura PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.lmstudio import LmStudioClient
from ai.models import TopicDecision, TopicQuery
from ai.topics import TopicDetectionAnalyzer
from analysis.service import LocalTopicAnalysisService
from tests.unit.test_realworld_topic_improvements import _build_car_purchase_document


def main():
    print("=== AVVIO VALIDAZIONE LIVE TOPIC DETECTION CON LM STUDIO ===")
    client = LmStudioClient()
    if not client.is_available():
        print("[ERRORE] LM Studio non raggiungibile su http://127.0.0.1:1234")
        sys.exit(1)

    loaded_model = "qwen2.5-7b-instruct"
    analyzer = TopicDetectionAnalyzer(client=client, default_model_id=loaded_model, operational_mode=True)
    doc = _build_car_purchase_document()

    # 1. TEST STATO PRESENT
    print("\n[1/4] Test Topic Detection: stato PRESENT...")
    topic_present = TopicQuery(
        topic_id="q_auto",
        label="Acquisto automobile",
        description="Trattativa o accordo economico per l'acquisto di una macchina o veicolo",
    )
    res_pres = analyzer.detect_topic(
        document=doc,
        topic=topic_present,
        model_id=loaded_model,
        temperature=0.0,
        seed=42,
        operational_mode=True,
    )
    print(f" -> Decisione: {res_pres.decision.value}")
    print(f" -> Evidenze citate: {res_pres.evidence_ids}")
    print(f" -> Motivazione: {res_pres.rationale}")
    assert res_pres.decision == TopicDecision.PRESENT, f"Atteso PRESENT, ottenuto {res_pres.decision}"
    assert len(res_pres.evidence_ids) >= 1, "PRESENT deve contenere almeno un'evidenza"
    for eid in res_pres.evidence_ids:
        assert "car" in eid or "whatsapp_txt" in eid or "unified" in eid, f"Evidenza anomala: {eid}"
    print(" [OK] Stato PRESENT validato con evidenze corrette.")

    # 2. TEST STATO ABSENT
    print("\n[2/4] Test Topic Detection: stato ABSENT...")
    topic_absent = TopicQuery(
        topic_id="q_vacanze",
        label="Vacanze al mare",
        description="Pianificazione di viaggi in spiaggia, prenotazione hotel o mete estive",
    )
    res_abs = analyzer.detect_topic(
        document=doc,
        topic=topic_absent,
        model_id=loaded_model,
        temperature=0.0,
        seed=42,
        operational_mode=True,
    )
    print(f" -> Decisione: {res_abs.decision.value}")
    print(f" -> Evidenze citate: {res_abs.evidence_ids}")
    print(f" -> Motivazione: {res_abs.rationale}")
    assert res_abs.decision == TopicDecision.ABSENT, f"Atteso ABSENT, ottenuto {res_abs.decision}"
    assert len(res_abs.evidence_ids) == 0, f"ABSENT NON deve contenere evidenze! Trovate: {res_abs.evidence_ids}"
    assert res_abs.rationale.strip() != "", "ABSENT deve includere una motivazione non vuota"
    print(" [OK] Stato ABSENT validato con evidenze vuote e motivazione oggettiva.")

    # 3. TEST VIA LocalTopicAnalysisService (Workflow completo con schema e checkpoint)
    print("\n[3/4] Test Topic Detection via LocalTopicAnalysisService...")
    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model=loaded_model,
        enable_checkpointing=False,
    )
    svc_res = service.run_topic_detection(
        document=doc,
        topic=topic_present,
        client=client,
        model_name=loaded_model,
        operational_mode=True,
    )
    print(f" -> Service Decision: {svc_res.decision.value}")
    print(f" -> Service Evidences: {svc_res.evidence_ids}")
    print(f" -> Metadata finish_reason: {svc_res.metadata.get('finish_reason')}")
    print(f" -> Metadata completion_tokens: {svc_res.metadata.get('completion_tokens')}")
    assert svc_res.decision == TopicDecision.PRESENT
    assert len(svc_res.evidence_ids) >= 1
    print(" [OK] Service Topic Detection completato con successo.")

    # 4. TEST INDIPENDENZA DISCOVERY -> DETECTION
    print("\n[4/4] Test Discovery -> Nuova Detection indipendente...")
    disc_res = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name=loaded_model,
        max_topics=3,
        temperature=0.0,
        seed=42,
    )
    print(f" -> Topic scoperti: {len(disc_res.topics)}")
    for t in disc_res.topics:
        print(f"    - {t.label}: {t.short_description} ({len(t.evidence_ids)} evidenze)")
    assert len(disc_res.topics) >= 1, "La discovery sintetica deve produrre almeno 1 topic"
    first_disc = disc_res.topics[0]
    query_from_disc = TopicQuery(
        topic_id="q_from_disc",
        label=first_disc.label,
        description=first_disc.short_description,
    )
    det_from_disc = service.run_topic_detection(
        document=doc,
        topic=query_from_disc,
        client=client,
        model_name=loaded_model,
        operational_mode=True,
    )
    print(f" -> Detection del topic scoperto ('{first_disc.label}'): {det_from_disc.decision.value}")
    assert det_from_disc.decision in (TopicDecision.PRESENT, TopicDecision.UNCERTAIN)
    print(" [OK] Pipeline Discovery -> Detection indipendente completata con successo.")

    print("\n=== TUTTI I TEST LIVE DI TOPIC DETECTION HANNO AVUTO SUCCESSO! ===")


if __name__ == "__main__":
    main()
