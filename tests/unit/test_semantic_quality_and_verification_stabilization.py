"""
tests/unit/test_semantic_quality_and_verification_stabilization.py
------------------------------------------------------------------
Test unitari deterministici per la stabilizzazione semantica end-to-end (03/11/2026):
1. Salvaguardia automatica `flash_attention=False` in `LmStudioClient.load_model` e `ensure_model_loaded`.
2. Serializzazione compatta JSONL con preservazione di `source_type` (STT/OCR/Vision) e `media_placeholder`.
3. Filtro deterministico post-validazione `filter_operational_discovered_topics` per saluti fàtici,
   placeholder multimediali privi di contenuto e negazioni esplicite.
4. Pulizia deterministica dei placeholder co-citati in `TopicDetectionAnalyzer.detect_topic`.
5. Consolidamento cross-chunk basato su stem di dominio specifici in `aggregate_topic_discovery`
   (senza fondere attività distinte come calcetto vs nuoto o topic numerati).
6. Metadati diagnostici strutturati (`jaccard_similarity`, `discrepancy_reason_code`) in `ConsistencyService`.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest

from ai.backend import FakeLocalLlmClient
from ai.chunking import aggregate_topic_discovery
from ai.lmstudio import LmStudioClient
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.serializer import (
    is_media_placeholder_text,
    is_phatic_greeting_text,
    serialize_document_compact_jsonl,
)
from ai.topics import (
    TopicDetectionAnalyzer,
    filter_operational_discovered_topics,
)
from analysis.consistency import ConsistencyService
from analysis.models import ConsistencyStatus
from tests.fixtures.synthetic_semantic_corpus import (
    SyntheticBenchmarkCase,
    build_synthetic_semantic_corpus,
)


def _get_case_by_id(case_id: str) -> SyntheticBenchmarkCase:
    for c in build_synthetic_semantic_corpus():
        if c.case_id == case_id:
            return c
    raise KeyError(case_id)


def test_synthetic_corpus_structure_and_splits():
    all_cases = build_synthetic_semantic_corpus()
    dev = [c for c in all_cases if c.split == "DEV"]
    holdout = [c for c in all_cases if c.split == "HOLDOUT"]
    assert len(dev) >= 8
    assert len(holdout) >= 6
    scenarios = {c.scenario for c in dev}
    assert {"I", "II", "III", "IV", "V_A", "V_B", "VI", "VII", "VIII"} <= scenarios


def test_media_placeholder_and_phatic_classifiers():
    assert is_media_placeholder_text("<Media omessi>") is True
    assert is_media_placeholder_text("IMG-20261015-WA0002.jpg (file allegato)") is True
    assert is_media_placeholder_text("PTT-20261015-WA0001.opus (file allegato)") is True
    assert is_media_placeholder_text("I messaggi e le chiamate sono crittografati end-to-end.") is True
    assert is_media_placeholder_text("Invio foto allegata") is False
    assert is_media_placeholder_text("Domani alle 15 abbiamo l'esame di Basi di Dati.") is False

    assert is_phatic_greeting_text("Ciao Marco! Come stai?") is True
    assert is_phatic_greeting_text("Tutto ok! Buona giornata allora") is True
    assert is_phatic_greeting_text("Ci vediamo domani, un abbraccio!") is True
    assert is_phatic_greeting_text("Messaggio 0 per verifica evidenze") is False
    assert is_phatic_greeting_text("Ho configurato il backup automatico del database PostgreSQL.") is False


def test_compact_jsonl_preserves_multimodal_source_type_and_marks_media_placeholders():
    case_vi = _get_case_by_id("DEV_VI_MULTIMODAL_STT_OCR_AND_BARE_IMG")
    payload, id_to_alias, _ = serialize_document_compact_jsonl(case_vi.document)
    lines = [
        json.loads(line)
        for line in payload.splitlines()
        if line.strip().startswith("{")
    ]
    by_id = {row["id"]: row for row in lines}

    a_m01_orig = id_to_alias["d6_m01::ORIGINAL_TEXT"]
    a_m01_stt = id_to_alias["d6_m01::STT_TRANSCRIPTION"]
    a_m03_orig = id_to_alias["d6_m03::ORIGINAL_TEXT"]
    a_m03_ocr = id_to_alias["d6_m03::OCR_TEXT"]
    a_m04_bare = id_to_alias["d6_m04::ORIGINAL_TEXT"]

    assert by_id[a_m01_orig].get("media_placeholder") is True
    assert by_id[a_m01_stt].get("source_type") == "STT_TRANSCRIPTION"
    assert by_id[a_m03_orig].get("media_placeholder") is True
    assert by_id[a_m03_ocr].get("source_type") == "OCR_TEXT"
    assert by_id[a_m04_bare].get("media_placeholder") is True


def test_filter_operational_discovered_topics_strips_placeholders_and_drops_spurious():
    case_vi = _get_case_by_id("DEV_VI_MULTIMODAL_STT_OCR_AND_BARE_IMG")
    raw_topics = (
        DiscoveredTopic(
            label="Sopralluogo cantiere via Verdi",
            short_description="Audio sul sopralluogo al cantiere di via Verdi.",
            evidence_ids=("d6_m01::ORIGINAL_TEXT", "d6_m01::STT_TRANSCRIPTION", "d6_m02::ORIGINAL_TEXT"),
        ),
        DiscoveredTopic(
            label="File allegato",
            short_description="Immagine allegata senza testo.",
            evidence_ids=("d6_m04::ORIGINAL_TEXT",),
        ),
    )
    filtered = filter_operational_discovered_topics(raw_topics, case_vi.document)
    assert len(filtered) == 1
    assert filtered[0].label == "Sopralluogo cantiere via Verdi"
    assert filtered[0].evidence_ids == ("d6_m01::STT_TRANSCRIPTION", "d6_m02::ORIGINAL_TEXT")

    case_ii = _get_case_by_id("DEV_II_PHATIC_NO_TOPIC")
    phatic_topics = (
        DiscoveredTopic(
            label="Saluto e benvenuto",
            short_description="I partecipanti si salutano.",
            evidence_ids=("d2_m01::ORIGINAL_TEXT", "d2_m02::ORIGINAL_TEXT"),
        ),
        DiscoveredTopic(
            label="Aggiornamento stato",
            short_description="Tutto bene grazie.",
            evidence_ids=("d2_m03::ORIGINAL_TEXT",),
        ),
    )
    assert filter_operational_discovered_topics(phatic_topics, case_ii.document) == ()

    case_iv = _get_case_by_id("DEV_IV_NEGATIONS_AND_PASSING")
    neg_topics = (
        DiscoveredTopic(
            label="Assenza di pratiche finanziarie",
            short_description="Non hanno mai chiesto un mutuo.",
            evidence_ids=("d4_m04::ORIGINAL_TEXT",),
        ),
        DiscoveredTopic(
            label="Richiesta mutuo bancario",
            short_description="Non abbiamo mai chiesto un mutuo.",
            evidence_ids=("d4_m04::ORIGINAL_TEXT", "d4_m05::ORIGINAL_TEXT"),
        ),
        DiscoveredTopic(
            label="Affitto appartamento bilocale",
            short_description="Bonifico per affitto e condominio.",
            evidence_ids=("d4_m01::ORIGINAL_TEXT", "d4_m02::ORIGINAL_TEXT"),
        ),
    )
    filtered_iv = filter_operational_discovered_topics(neg_topics, case_iv.document)
    assert len(filtered_iv) == 1
    assert filtered_iv[0].label == "Affitto appartamento bilocale"


def test_detection_strips_co_cited_media_placeholder_when_substantive_present():
    case_vi = _get_case_by_id("DEV_VI_MULTIMODAL_STT_OCR_AND_BARE_IMG")
    query = TopicQuery(
        topic_id="q_test_vi",
        label="Preventivo impianto elettrico",
        description="Fattura o preventivo per rifacimento impianto elettrico.",
    )
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": ["E004", "E005"],  # d6_m03::ORIGINAL_TEXT (placeholder) + d6_m03::OCR_TEXT
            "rationale": "Il testo OCR conferma il preventivo impianto elettrico.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    res = analyzer.detect_topic(case_vi.document, query)
    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == ("d6_m03::OCR_TEXT",)


def test_aggregate_topic_discovery_consolidates_domain_stems_and_preserves_distinct():
    r0 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(
                label="Biglietti del treno per Bologna",
                short_description="Acquisto dei biglietti del treno per andare a Bologna sabato.",
                evidence_ids=("d1_m01::ORIGINAL_TEXT", "d1_m02::ORIGINAL_TEXT"),
            ),
            DiscoveredTopic(
                label="Problemi batteria portatile",
                short_description="La batteria del portatile si scarica subito.",
                evidence_ids=("d1_m08::ORIGINAL_TEXT",),
            ),
            DiscoveredTopic(
                label="Partita di calcetto del giovedì",
                short_description="Prenotazione campo da calcetto al centro sportivo.",
                evidence_ids=("d3_m04::ORIGINAL_TEXT",),
            ),
        ),
        provenance_document_id="doc::chunk_0",
    )
    r1 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(
                label="Viaggio ferroviario a Bologna",
                short_description="Ritorno in treno da Bologna la domenica sera.",
                evidence_ids=("d1_m12::ORIGINAL_TEXT",),
            ),
            DiscoveredTopic(
                label="Sostituzione batteria del laptop",
                short_description="Preventivo assistenza per sostituire la batteria del portatile.",
                evidence_ids=("d1_m09::ORIGINAL_TEXT", "d1_m10::ORIGINAL_TEXT"),
            ),
            DiscoveredTopic(
                label="Iscrizione al corso di nuoto",
                short_description="Iscrizione in piscina per il corso di nuoto serale.",
                evidence_ids=("d3_m07::ORIGINAL_TEXT",),
            ),
        ),
        provenance_document_id="doc::chunk_1",
    )
    agg = aggregate_topic_discovery([r0, r1], parent_document_id="doc", max_topics=10)
    assert len(agg.topics) == 4
    eids_sets = [set(t.evidence_ids) for t in agg.topics]
    assert {"d1_m01::ORIGINAL_TEXT", "d1_m02::ORIGINAL_TEXT", "d1_m12::ORIGINAL_TEXT"} in eids_sets
    assert {"d1_m08::ORIGINAL_TEXT", "d1_m09::ORIGINAL_TEXT", "d1_m10::ORIGINAL_TEXT"} in eids_sets
    assert {"d3_m04::ORIGINAL_TEXT"} in eids_sets
    assert {"d3_m07::ORIGINAL_TEXT"} in eids_sets

    # Verifica che topic con compiti distinti ed evidenze disgiunte NON vengano fusi forzatamente
    r_devops = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(
                label="Correzione bug autenticazione backend",
                short_description="Davide ha aperto una pull request per correggere un bug di autenticazione nel backend.",
                evidence_ids=("d5a_m01::ORIGINAL_TEXT",),
            ),
            DiscoveredTopic(
                label="Problemi nella pipeline CI Docker",
                short_description="Martina ha notato che la pipeline CI fallisce durante il build dell'immagine Docker del server.",
                evidence_ids=("d5a_m02::ORIGINAL_TEXT",),
            ),
            DiscoveredTopic(
                label="Aggiornamento Dockerfile Python",
                short_description="Davide aggiorna subito il Dockerfile con la versione corretta di Python prima di fare il deploy in staging.",
                evidence_ids=("d5a_m03::ORIGINAL_TEXT",),
            ),
            DiscoveredTopic(
                label="Merge pull request su GitHub",
                short_description="Martina suggerisce di fare il merge su GitHub appena il container Docker compila.",
                evidence_ids=("d5a_m04::ORIGINAL_TEXT",),
            ),
        ),
        provenance_document_id="doc_devops::chunk_0",
    )
    agg_devops = aggregate_topic_discovery([r_devops], parent_document_id="doc_devops", max_topics=10)
    # I task con evidenze disgiunte e domini/azioni differenti non vengono unificati artificialmente
    assert len(agg_devops.topics) >= 2


def test_consolidation_negative_synthetic_pairs_preserve_distinct_topics():
    """
    Verifica con test sintetici NEGATIVI che compiti, azioni o domini distinti
    NON vengano mai impropriamente fusi per alzare artificialmente la precisione:
    1. Due problemi distinti dello stesso database (Deadlock vs Migrazione);
    2. Acquisto vs restituzione dello stesso prodotto (Acquisto vs Reso);
    3. Due viaggi distinti verso la stessa città (Lavoro vs Vacanza a Milano);
    4. Stesso termine tecnico ma task diversi (Backup vs Ottimizzazione query);
    5. Stessa chat, evidenze disgiunte (Bug fix vs Merge PR).
    """
    # 1. Due problemi distinti dello stesso database
    r_db = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic("Deadlock transazioni PostgreSQL", "Analisi blocchi reciproci.", ("m01", "m02")),
            DiscoveredTopic("Migrazione schema PostgreSQL", "Esecuzione script DDL tabelle.", ("m03", "m04")),
        ),
        provenance_document_id="doc_db",
    )
    agg_db = aggregate_topic_discovery([r_db], "doc_db")
    assert len(agg_db.topics) == 2

    # 2. Acquisto vs restituzione dello stesso prodotto
    r_prod = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic("Acquisto stampante laser da ufficio", "Ordine e fattura stampante.", ("m05",)),
            DiscoveredTopic("Restituzione stampante laser guasta", "Reso fornitore stampante.", ("m06",)),
        ),
        provenance_document_id="doc_prod",
    )
    agg_prod = aggregate_topic_discovery([r_prod], "doc_prod")
    assert len(agg_prod.topics) == 2

    # 3. Due viaggi distinti verso la stessa città
    r_city = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic("Viaggio di lavoro a Milano", "Riunione consulenza clienti.", ("m07",)),
            DiscoveredTopic("Weekend di vacanza a Milano", "Musei e relax con amici.", ("m08",)),
        ),
        provenance_document_id="doc_city",
    )
    agg_city = aggregate_topic_discovery([r_city], "doc_city")
    assert len(agg_city.topics) == 2

    # 4. Stesso termine tecnico ma task diversi
    r_pg = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic("Backup database PostgreSQL", "Configurazione dump notturno.", ("m09",)),
            DiscoveredTopic("Ottimizzazione query PostgreSQL", "Creazione indici lenti.", ("m10",)),
        ),
        provenance_document_id="doc_pg",
    )
    agg_pg = aggregate_topic_discovery([r_pg], "doc_pg")
    assert len(agg_pg.topics) == 2

    # 5. Stessa chat, evidenze disgiunte
    r_disj = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic("Correzione bug autenticazione", "Bug fix login backend.", ("m11",)),
            DiscoveredTopic("Merge pull request su GitHub", "Merge PR completato.", ("m12",)),
        ),
        provenance_document_id="doc_disj",
    )
    agg_disj = aggregate_topic_discovery([r_disj], "doc_disj")
    assert len(agg_disj.topics) == 2


def test_filter_operational_discovered_topics_preserves_legitimate_cancellation_topic():
    """Verifica che un topic legittimo sull'annullamento/rinuncia non venga eliminato dal filtro negazioni."""
    from tests.fixtures.synthetic_semantic_corpus import _make_doc

    doc = _make_doc(
        "doc_cancellation",
        [
            ("c_m01", "Elena", "1", "Ho annullato tutto l'ordine dei biglietti perché lo spettacolo è stato rinviato.", None, None),
        ],
    )

    cancellation_topic = DiscoveredTopic(
        label="Annullamento ordine biglietti",
        short_description="Elena ha annullato l'ordine dei biglietti dello spettacolo.",
        evidence_ids=("c_m01::ORIGINAL_TEXT",),
    )
    false_affirmative_topic = DiscoveredTopic(
        label="Acquisto biglietti per lo spettacolo",
        short_description="Acquisto completato dei biglietti.",
        evidence_ids=("c_m01::ORIGINAL_TEXT",),
    )

    filtered = filter_operational_discovered_topics((cancellation_topic, false_affirmative_topic), doc)
    assert len(filtered) == 1
    assert filtered[0].label == "Annullamento ordine biglietti"


def test_filter_operational_discovered_topics_does_not_treat_incidental_res_substring_as_cancellation():
    """Verifica che parole contenenti la sottostringa 'res' (prestito, presentazione, restauro)
    non eludano il filtro negazioni spacciandosi per topic di annullamento/reso."""
    from tests.fixtures.synthetic_semantic_corpus import _make_doc

    doc = _make_doc(
        "doc_negation_incidental_res",
        [
            ("m01", "Mario", "1", "Non abbiamo mai chiesto un prestito bancario.", None, None),
            ("m02", "Anna", "2", "Non abbiamo fatto alcuna presentazione del progetto.", None, None),
            ("m03", "Luca", "3", "Non abbiamo fatto alcun lavoro di restauro della facciata.", None, None),
            ("m04", "Giulia", "4", "Ho restituito il pacco difettoso e richiesto il reso.", None, None),
        ],
    )

    t_prestito = DiscoveredTopic(
        label="Prestito bancario",
        short_description="Richiesta di prestito",
        evidence_ids=("m01::ORIGINAL_TEXT",),
    )
    t_presentazione = DiscoveredTopic(
        label="Presentazione del progetto",
        short_description="Presentazione",
        evidence_ids=("m02::ORIGINAL_TEXT",),
    )
    t_restauro = DiscoveredTopic(
        label="Restauro della facciata",
        short_description="Lavori di restauro",
        evidence_ids=("m03::ORIGINAL_TEXT",),
    )
    t_reso = DiscoveredTopic(
        label="Reso del prodotto difettoso",
        short_description="Giulia ha richiesto il reso",
        evidence_ids=("m04::ORIGINAL_TEXT",),
    )

    filtered = filter_operational_discovered_topics((t_prestito, t_presentazione, t_restauro, t_reso), doc)
    # Solo il tema autentico di reso deve sopravvivere; i tre falsi affermativi su negazioni devono essere filtrati
    assert [t.label for t in filtered] == ["Reso del prodotto difettoso"]



def test_phatic_classifier_rejects_substantive_phrases():
    """Verifica che frasi con contenuti reali ('Lavoro domani') non siano scambiate per saluti fàtici."""
    assert is_phatic_greeting_text("Lavoro domani") is False
    assert is_phatic_greeting_text("Domani mattina alle 9 ho una riunione") is False
    assert is_phatic_greeting_text("Bene, ci vediamo domani un abbraccio") is True
    assert is_phatic_greeting_text("Ciao Marco! Come stai?") is True
    assert is_phatic_greeting_text("Grazie mille, a dopo!") is True


def test_lmstudio_ensure_model_loaded_reloads_when_flash_attention_true_detected(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def mock_urlopen(req: Any, timeout: float = 10.0) -> Any:
        url = req.full_url
        method = req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((method, url, body))

        class Resp:
            def __init__(self, payload: dict[str, Any], status: int = 200):
                self.status = status
                self._bytes = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._bytes

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        if method == "GET" and "/v1/models" in url and "/api/v1/models" not in url:
            return Resp({"data": [{"id": "qwen2.5-7b-instruct"}]})
        if method == "GET" and "/v1/models" in url and "/api/v1/models" not in url:
            return Resp({"data": [{"id": "meta-llama-3-8b-instruct"}]})
        if method == "GET" and "/api/v1/models" in url:
            return Resp({
                "models": [{
                    "key": "qwen2.5-7b-instruct",
                    "display_name": "Qwen2.5 7B Instruct",
                    "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                    "loaded_instances": [{
                        "id": "qwen2.5-7b-instruct",
                        "config": {"context_length": 8192, "flash_attention": True},
                    }],
                }]
            })
        if method == "POST" and "/api/v1/models/unload" in url:
            return Resp({"status": "unloaded"})
        if method == "POST" and "/api/v1/models/load" in url:
            return Resp({
                "status": "loaded",
                "instance_id": "qwen2.5-7b-instruct",
                "model": "qwen2.5-7b-instruct",
                "load_config": {"context_length": 8192, "flash_attention": False},
            })
        raise AssertionError(f"Unexpected URL: {method} {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    active_id = client.ensure_model_loaded("qwen2.5-7b-instruct", context_length=8192)
    assert active_id == "qwen2.5-7b-instruct"

    unload_calls = [c for c in calls if "/api/v1/models/unload" in c[1]]
    load_calls = [c for c in calls if "/api/v1/models/load" in c[1]]
    assert len(unload_calls) == 1
    assert len(load_calls) == 1
    assert load_calls[0][2] == {
        "model": "qwen2.5-7b-instruct",
        "context_length": 8192,
        "echo_load_config": True,
        "flash_attention": False,
    }


def test_lmstudio_ensure_model_loaded_keeps_non_qwen_model_without_unloading(monkeypatch: pytest.MonkeyPatch):
    """Verifica di non regressione: modelli non-Qwen già caricati non vengono scaricati arbitrariamente."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def mock_urlopen(req: Any, timeout: float = 10.0) -> Any:
        url = req.full_url
        method = req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((method, url, body))

        class Resp:
            def __init__(self, payload: dict[str, Any], status: int = 200):
                self.status = status
                self._bytes = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._bytes

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        if method == "GET" and "/v1/models" in url and "/api/v1/models" not in url:
            return Resp({"data": [{"id": "meta-llama-3.1-8b-instruct"}]})
        if method == "GET" and "/api/v1/models" in url:
            return Resp({
                "models": [{
                    "key": "meta-llama-3.1-8b-instruct",
                    "display_name": "Llama 3.1 8B Instruct",
                    "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                    "loaded_instances": [{
                        "id": "meta-llama-3.1-8b-instruct",
                        "config": {"context_length": 8192, "flash_attention": True},
                    }],
                }]
            })
        raise AssertionError(f"Chiamata inattesa: {method} {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    active_id = client.ensure_model_loaded("meta-llama-3.1-8b-instruct", context_length=8192)
    assert active_id == "meta-llama-3.1-8b-instruct"
    unload_calls = [c for c in calls if "/api/v1/models/unload" in c[1]]
    assert len(unload_calls) == 0


def test_lmstudio_ensure_model_loaded_keeps_instance_when_config_is_none(monkeypatch: pytest.MonkeyPatch):
    """Verifica che un'istanza con config non disponibile o None non venga scaricata."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def mock_urlopen(req: Any, timeout: float = 10.0) -> Any:
        url = req.full_url
        method = req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((method, url, body))

        class Resp:
            def __init__(self, payload: dict[str, Any], status: int = 200):
                self.status = status
                self._bytes = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._bytes

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        if method == "GET" and "/v1/models" in url and "/api/v1/models" not in url:
            return Resp({"data": [{"id": "meta-llama-3-8b-instruct"}]})
        if method == "GET" and "/api/v1/models" in url:
            return Resp({
                "models": [{
                    "key": "qwen2.5-7b-instruct",
                    "display_name": "Qwen2.5 7B Instruct",
                    "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                    "loaded_instances": [{
                        "id": "qwen2.5-7b-instruct",
                        "config": None,
                    }],
                }]
            })
        raise AssertionError(f"Chiamata inattesa: {method} {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    active_id = client.ensure_model_loaded("qwen2.5-7b-instruct", context_length=8192)
    assert active_id == "qwen2.5-7b-instruct"
    unload_calls = [c for c in calls if "/api/v1/models/unload" in c[1]]
    assert len(unload_calls) == 0


def test_lmstudio_ensure_model_loaded_respects_explicit_flash_attention_true(monkeypatch: pytest.MonkeyPatch):
    """Verifica che l'opzione esplicita flash_attention=True preservi l'istanza attiva senza unload."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def mock_urlopen(req: Any, timeout: float = 10.0) -> Any:
        url = req.full_url
        method = req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((method, url, body))

        class Resp:
            def __init__(self, payload: dict[str, Any], status: int = 200):
                self.status = status
                self._bytes = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._bytes

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        if method == "GET" and "/v1/models" in url and "/api/v1/models" not in url:
            return Resp({"data": [{"id": "meta-llama-3-8b-instruct"}]})
        if method == "GET" and "/api/v1/models" in url:
            return Resp({
                "models": [{
                    "key": "qwen2.5-7b-instruct",
                    "display_name": "Qwen2.5 7B Instruct",
                    "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                    "loaded_instances": [{
                        "id": "qwen2.5-7b-instruct",
                        "config": {"context_length": 8192, "flash_attention": True},
                    }],
                }]
            })
        raise AssertionError(f"Chiamata inattesa: {method} {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    active_id = client.ensure_model_loaded("qwen2.5-7b-instruct", context_length=8192, flash_attention=True)
    assert active_id == "qwen2.5-7b-instruct"
    unload_calls = [c for c in calls if "/api/v1/models/unload" in c[1]]
    assert len(unload_calls) == 0


def test_lmstudio_load_model_retries_without_flash_attention_on_http_400(monkeypatch: pytest.MonkeyPatch):
    """Verifica il fallback resiliente quando un server legacy rifiuta il campo 'flash_attention' con HTTP 400."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    attempt = 0

    def mock_urlopen(req: Any, timeout: float = 10.0) -> Any:
        nonlocal attempt
        attempt += 1
        url = req.full_url
        method = req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((method, url, body))

        class Resp:
            def __init__(self, payload: dict[str, Any], status: int = 200):
                self.status = status
                self._bytes = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._bytes

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        if method == "POST" and "/api/v1/models/load" in url:
            if attempt == 1:
                # Simula rifiuto HTTP 400 del parametro 'flash_attention'
                import urllib.error
                import io
                raise urllib.error.HTTPError(
                    url, 400, "Unknown field 'flash_attention'", {}, io.BytesIO(b'{"error": "Unknown field flash_attention"}')
                )
            return Resp({
                "status": "loaded",
                "instance_id": "qwen2.5-7b-instruct",
                "model": "qwen2.5-7b-instruct",
                "load_config": {"context_length": 8192},
            })
        raise AssertionError(f"Chiamata inattesa: {method} {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct", context_length=8192, flash_attention=False)
    assert res.status.value in ("LOADED", "loaded", "already_loaded")
    assert len(calls) == 2
    assert "flash_attention" in calls[0][2]
    assert "flash_attention" not in calls[1][2]


def test_consistency_service_adds_jaccard_and_discrepancy_reason_code():
    disc_topic = DiscoveredTopic(
        label="Esame universitario di Basi di Dati",
        short_description="Sessione di studio per l'esame di Basi di Dati.",
        evidence_ids=("e1", "e2"),
    )
    q = TopicQuery(topic_id="q1", label=disc_topic.label, description=disc_topic.short_description)
    det_ok = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.PRESENT,
        evidence_ids=("e2", "e3"),
        rationale="Confermato.",
        provenance_document_id="doc_1",
    )
    c_ok = ConsistencyService.evaluate("doc_1", disc_topic, det_ok, "run_disc", "run_det")
    assert c_ok.status == ConsistencyStatus.CONSISTENT
    assert c_ok.metadata["discrepancy_reason_code"] == "NONE"
    assert c_ok.metadata["jaccard_similarity"] == round(1 / 3, 4)

    det_abs = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale="Assente.",
        provenance_document_id="doc_1",
    )
    c_abs = ConsistencyService.evaluate("doc_1", disc_topic, det_abs, "run_disc", "run_det2")
    assert c_abs.status == ConsistencyStatus.DISCREPANCY_ABSENT
    assert c_abs.metadata["discrepancy_reason_code"] == "DETECTION_CONFIRMED_ABSENT"
    assert c_abs.metadata["jaccard_similarity"] == 0.0
