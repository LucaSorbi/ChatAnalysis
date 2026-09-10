"""
importer/cellebrite_xml.py
--------------------------
Importer concreto per esportazioni messaggi Cellebrite in formato XML (report UFDR).

Sorgente supportata
-------------------
File:   report.xml (o esportazioni XML Cellebrite UFDR compatibili)
Tipo:   File XML contenente un root element <DumpFile type="UFDR"> con metadati
        di dispositivo (<DeviceInfo>) e collezione messaggi (<InstantMessages>).
Tag caratteristici osservati nel campione:
    <DumpFile type="UFDR" version="2.0">
      <DeviceInfo>...</DeviceInfo>
      <InstantMessages>
        <Message id="0">
          <Timestamp>2024-04-23T20:30:15+00:00</Timestamp>
          <Sender>+39 000 0000001</Sender>
          <Body>Messaggio di test sintetico 08</Body>
          <Deleted>false</Deleted>
        </Message>
        ...
      </InstantMessages>
    </DumpFile>

Sicurezza del parsing (Input non fidato)
----------------------------------------
Poiché le estrazioni forensi costituiscono input non fidato, il parsing utilizza
la libreria specializzata `defusedxml` con `forbid_dtd=True`:
  - DTD e DOCTYPE vietati (sollevano DTDForbidden);
  - Entity expansion vietata (Billion Laughs / DoS bloccati alla radice);
  - External entities (XXE) vietate;
  - Riconoscimento ed estrazione privi di accessi a risorse esterne o di rete.

Streaming e rilascio memoria
----------------------------
  - Il file viene letto progressivamente tramite `defusedxml.ElementTree.iterparse`
    senza caricare l'intero DOM in memoria.
  - Al termine di ciascun elemento `<Message>` (evento 'end'), i campi vengono
    estratti nel RawRecord, l'elemento viene ripulito via `elem.clear()`, e rimosso
    dal genitore via `parent.remove(elem)` PRIMA di cedere il controllo con `yield`.
  - Questo impedisce sia il consumo proporzionale al numero complessivo dei messaggi,
    sia l'accumulo di nodi orfani vuoti nel nodo padre del parser ElementTree.

Preservazione dei tipi source-level
-----------------------------------
A differenza del formato JSON (che supporta tipi nativi booleani/numerici), XML
rappresenta nativamente i valori come stringhe testuali o elementi vuoti (None).
In conformità con il principio di source-faithfulness:
  - <Deleted>false</Deleted> è preservato come stringa "false" (non booleano);
  - <Timestamp> è preservato come stringa ISO 8601 grezza;
  - <Body /> vuoto è preservato come None;
  - Nessuna conversione o normalizzazione di tipo viene effettuata a questo livello.

Questioni aperte rimandate ai layer successivi
----------------------------------------------
  - ChatId / JID mapping;
  - group_participant_A entity resolution;
  - Timezone conversion (timestamp grezzo intatto);
  - Deleted semantics ("false" / "true");
  - Media physical validation.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterator

import defusedxml.ElementTree as dET
from defusedxml.common import DefusedXmlException

from importer.base import BaseImporter
from importer.models import RawRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Costanti strutturali
# ---------------------------------------------------------------------------

_SOURCE_NAME = "cellebrite_xml"
_IMPORTER_VERSION = "0.1.0"

# Tag radice e contenitore attesi nelle esportazioni UFDR Cellebrite
_ROOT_TAG = "DumpFile"
_CONTAINER_TAG = "InstantMessages"
_MESSAGE_TAG = "Message"


def _extract_local_tag(tag: str) -> str:
    """Rimuove l'eventuale namespace URI da un tag ElementTree (es. '{uri}Tag' -> 'Tag')."""
    return tag.split("}")[-1] if "}" in tag else tag


def _xml_element_to_dict(elem: Any) -> Any:
    """
    Converte ricorsivamente un elemento XML in una struttura dati fedele (dict/list/str/None).

    Preserva:
      - Attributi in '@attributes' (e attributo 'id' come chiave diretta se presente);
      - Testo grezzo dei child elements;
      - Elementi vuoti (<Tag />) come None;
      - Elementi child ripetuti come liste ordinate per preservare cardinalità e sequenza;
      - Identità di namespace XML originale quando presente (notazione Clark '{uri}Tag'),
        mantenendo il tag semplice 'Tag' per elementi senza namespace.
    """
    # Se l'elemento non ha figli e nessun attributo, restituisce direttamente il testo
    if len(elem) == 0 and not elem.attrib:
        return elem.text

    res: dict[str, Any] = {}

    if elem.attrib:
        res["@attributes"] = dict(elem.attrib)
        if "id" in elem.attrib and "id" not in res:
            res["id"] = elem.attrib["id"]

    # Raccoglie i figli usando il tag originale (notazione Clark se ha namespace, stringa semplice altrimenti)
    children_dict: dict[str, Any] = {}
    for child in elem:
        tag_key = child.tag
        child_val = _xml_element_to_dict(child)

        if tag_key in children_dict:
            # Preserva elementi ripetuti come lista ordinata
            current = children_dict[tag_key]
            if not isinstance(current, list):
                children_dict[tag_key] = [current]
            children_dict[tag_key].append(child_val)
        else:
            children_dict[tag_key] = child_val

    res.update(children_dict)

    # Se c'è testo residuo in un elemento con attributi
    if elem.text and elem.text.strip() and len(elem) == 0:
        res["#text"] = elem.text

    return res


class CellebriteXmlImporter(BaseImporter):
    """
    Importer concreto per esportazioni messaggi Cellebrite in formato XML (report UFDR).

    Emette RawRecord per ciascun elemento <Message>:
      - source_name = "cellebrite_xml"
      - record_type = "message"
      - source_record_id = attributo nativo 'id' o fallback deterministico "message:<idx>"
      - raw_fields = mapping fedele con tipi testuali grezzi e strutture congelate ricorsivamente
      - media_reference = None per il dataset osservato (nessun tag media presente)
      - metadata = contesto di provenienza (xml_index, tabella, formato, versione importer)
    """

    @property
    def source_name(self) -> str:
        return _SOURCE_NAME

    # ------------------------------------------------------------------
    # can_import — riconoscimento strutturale non-distruttivo
    # ------------------------------------------------------------------

    def can_import(self, source_path: Path) -> bool:
        """
        True se source_path è un file XML Cellebrite UFDR compatibile.

        Verifica strutturalmente in streaming l'intestazione del documento:
        - Root tag <DumpFile> con attributo type="UFDR" (o contenitore <InstantMessages>);
        - Nessuna dipendenza dall'estensione del file;
        - Parsing sicuro con defusedxml (policy esplicita: forbid_dtd=True,
          forbid_entities=True, forbid_external=True);
        - Cattura unicamente eccezioni previste di I/O, parsing o sicurezza XML.
        """
        if not source_path.exists() or not source_path.is_file():
            return False

        try:
            with open(source_path, "rb") as f:
                # Ispezione streaming sicura dei primi eventi: richiede root DumpFile UFDR
                # e presenza strutturale di InstantMessages o Message
                has_ufdr_root = False
                for event, elem in dET.iterparse(
                    f,
                    events=("start",),
                    forbid_dtd=True,
                    forbid_entities=True,
                    forbid_external=True,
                ):
                    local_tag = _extract_local_tag(elem.tag)
                    if local_tag == _ROOT_TAG:
                        if elem.attrib.get("type", "").upper() == "UFDR":
                            has_ufdr_root = True
                    elif has_ufdr_root and local_tag in (_CONTAINER_TAG, _MESSAGE_TAG):
                        return True
                    elif not has_ufdr_root and local_tag in (_CONTAINER_TAG, _MESSAGE_TAG):
                        return True
                return False
        except (OSError, dET.ParseError, DefusedXmlException, UnicodeDecodeError):
            # Errori di I/O, file non XML o malformati, violazioni DTD/XXE -> False
            return False

    # ------------------------------------------------------------------
    # import_records — generatore principale streaming
    # ------------------------------------------------------------------

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        """
        Legge il report XML Cellebrite in streaming ed emette RawRecord per ogni messaggio.

        Raises
        ------
        FileNotFoundError
            Se source_path non esiste.
        ValueError
            Se source_path non è riconosciuto come XML Cellebrite compatibile
            o se la struttura XML è malformata/non valida.
        """
        self.validate_source(source_path)
        source_str = str(source_path.resolve())

        try:
            with open(source_path, "rb") as f:
                stack: list[Any] = []
                msg_idx = 0
                for event, elem in dET.iterparse(
                    f,
                    events=("start", "end"),
                    forbid_dtd=True,
                    forbid_entities=True,
                    forbid_external=True,
                ):
                    if event == "start":
                        stack.append(elem)
                        continue

                    # event == "end"
                    local_tag = _extract_local_tag(elem.tag)

                    if local_tag == _MESSAGE_TAG:
                        # 1. Determinazione source_record_id: nativo 'id' > fallback 'message:<idx>'
                        native_id = elem.attrib.get("id")
                        if native_id is not None and str(native_id).strip():
                            record_id = str(native_id).strip()
                        else:
                            record_id = f"message:{msg_idx}"

                        # 2. Estrazione campi raw fedele (Clark notation preservata se con namespace)
                        fields_dict = _xml_element_to_dict(elem)
                        if not isinstance(fields_dict, dict):
                            fields_dict = {"text": fields_dict}

                        # 3. Nessuna logica speculativa su media XML non osservati:
                        # Nel dataset UFDR reale non esistono attachment nodes; media_reference è None.
                        media_ref: str | None = None

                        # 4. Creazione RawRecord (congelamento profondo automatico)
                        record = RawRecord(
                            source_name=_SOURCE_NAME,
                            source_path=source_str,
                            source_record_id=record_id,
                            record_type="message",
                            raw_fields=fields_dict,
                            media_reference=media_ref,
                            metadata={
                                "table": _CONTAINER_TAG,
                                "format": "xml_ufdr",
                                "xml_index": msg_idx,
                                "importer": "CellebriteXmlImporter",
                                "importer_version": _IMPORTER_VERSION,
                            },
                        )

                        # 5. Rilascio tempestivo della memoria PRIMA della sospensione del generatore:
                        # elem.clear() svuota l'elemento corrente;
                        # parent.remove(elem) rimuove il riferimento dall'elemento contenitore
                        # impedendo l'accumulo di migliaia di nodi vuoti nel parent.
                        parent = stack[-2] if len(stack) >= 2 else None
                        elem.clear()
                        if parent is not None:
                            parent.remove(elem)

                        stack.pop()
                        msg_idx += 1
                        yield record
                        continue

                    # Tutti gli altri elementi terminati: pop dallo stack
                    stack.pop()

        except (dET.ParseError, DefusedXmlException) as exc:
            raise ValueError(f"Errore durante il parsing del file XML {source_path}: {exc}") from exc
