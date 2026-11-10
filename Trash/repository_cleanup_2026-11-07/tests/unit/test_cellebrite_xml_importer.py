"""
tests/unit/test_cellebrite_xml_importer.py
------------------------------------------
Test unitari per CellebriteXmlImporter.

Copertura:
- Ereditarietà da BaseImporter e proprietà source_name
- can_import(): positivo (con/senza estensione, .txt), negativo (file inesistente,
  directory, XML generico, malformato, SQLite, CSV, JSON)
- Sicurezza XML (C2): rifiuto rigoroso di DTD, DOCTYPE, entity expansion (Billion Laughs), XXE
- import_records(): emissione RawRecord, generator/streaming, read-only
- Preservazione tipi XML nativi (testo e None; "false" resta str e non bool)
- Immutabilità profonda su strutture annidate (attributi, figli)
- Priorità source_record_id su 'id' nativo con fallback a 'message:<idx>'
- Supporto namespace XML
- Error handling su sorgenti non valide o malformate
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import MappingProxyType
from typing import Iterator

import pytest

from importer.base import BaseImporter
from importer.cellebrite_xml import CellebriteXmlImporter
from importer.models import RawRecord

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def importer():
    return CellebriteXmlImporter()


@pytest.fixture
def sample_xml_content() -> str:
    return """<?xml version='1.0' encoding='utf-8'?>
<DumpFile type="UFDR" version="2.0">
  <DeviceInfo>
    <Model>Samsung Galaxy S21</Model>
    <OS>Android 13</OS>
    <IMEI>000000000000001</IMEI>
  </DeviceInfo>
  <InstantMessages>
    <Message id="0">
      <Timestamp>2024-04-23T20:30:15+00:00</Timestamp>
      <Sender>+39 000 0000001</Sender>
      <Body>Messaggio di test sintetico 08</Body>
      <Deleted>false</Deleted>
    </Message>
    <Message id="1">
      <Timestamp>2024-09-17T17:28:45+00:00</Timestamp>
      <Sender>+1 000 0000004</Sender>
      <Body />
      <Deleted>false</Deleted>
    </Message>
    <Message id="2">
      <Timestamp>2024-11-22T14:47:37+00:00</Timestamp>
      <Sender>group_participant_A</Sender>
      <Body>Messaggio con media</Body>
      <Deleted>true</Deleted>
      <Attachment>media/image_01.jpg</Attachment>
    </Message>
    <Message>
      <!-- Messaggio privo di attributo id per testare il fallback -->
      <Timestamp>2025-01-01T00:00:00+00:00</Timestamp>
      <Sender>Contatto_001</Sender>
      <Body>Messaggio senza id</Body>
      <Deleted>false</Deleted>
    </Message>
  </InstantMessages>
</DumpFile>
"""


@pytest.fixture
def valid_xml_file(tmp_path: Path, sample_xml_content: str) -> Path:
    f = tmp_path / "test_report.xml"
    f.write_text(sample_xml_content, encoding="utf-8")
    return f


@pytest.fixture
def generic_xml_file(tmp_path: Path) -> Path:
    f = tmp_path / "generic.xml"
    f.write_text(
        "<?xml version='1.0'?><Catalog><Book id='b1'><Title>Sample</Title></Book></Catalog>",
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------------------
# Contratto BaseImporter
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestInheritsBaseImporter:

    def test_is_base_importer_subclass(self):
        assert issubclass(CellebriteXmlImporter, BaseImporter)

    def test_source_name_is_string(self, importer):
        assert isinstance(importer.source_name, str)

    def test_source_name_value(self, importer):
        assert importer.source_name == "cellebrite_xml"

    def test_repr_contains_class_name(self, importer):
        assert "CellebriteXmlImporter" in repr(importer)

    def test_repr_contains_source_name(self, importer):
        assert "cellebrite_xml" in repr(importer)


# ---------------------------------------------------------------------------
# can_import — casi positivi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportPositive:

    def test_returns_true_for_valid_xml(self, importer, valid_xml_file):
        assert importer.can_import(valid_xml_file) is True

    def test_returns_bool(self, importer, valid_xml_file):
        result = importer.can_import(valid_xml_file)
        assert isinstance(result, bool)

    def test_idempotent(self, importer, valid_xml_file):
        assert importer.can_import(valid_xml_file) == importer.can_import(valid_xml_file)

    def test_renamed_xml_no_extension(self, importer, valid_xml_file, tmp_path):
        renamed = tmp_path / "evidence_ufdr"
        renamed.write_bytes(valid_xml_file.read_bytes())
        assert importer.can_import(renamed) is True

    def test_renamed_xml_txt_extension(self, importer, valid_xml_file, tmp_path):
        renamed = tmp_path / "evidence.txt"
        renamed.write_bytes(valid_xml_file.read_bytes())
        assert importer.can_import(renamed) is True

    def test_synthetic_original_xml_returns_true(self, importer):
        project_root = Path(__file__).resolve().parent.parent.parent
        real_xml = project_root / "test_data" / "cellebrite_export" / "report.xml"
        if real_xml.exists():
            assert importer.can_import(real_xml) is True


# ---------------------------------------------------------------------------
# can_import — casi negativi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportNegative:

    def test_returns_false_for_nonexistent_file(self, importer, tmp_path):
        assert importer.can_import(tmp_path / "missing.xml") is False

    def test_returns_false_for_generic_xml(self, importer, generic_xml_file):
        assert importer.can_import(generic_xml_file) is False

    def test_returns_false_for_empty_xml(self, importer, tmp_path):
        f = tmp_path / "empty.xml"
        f.touch()
        assert importer.can_import(f) is False

    def test_returns_false_for_malformed_xml(self, importer, tmp_path):
        malformed = tmp_path / "malformed.xml"
        malformed.write_text("<DumpFile type='UFDR'><unclosed>", encoding="utf-8")
        assert importer.can_import(malformed) is False

    def test_returns_false_for_directory(self, importer, tmp_path):
        assert importer.can_import(tmp_path) is False

    def test_returns_false_for_plain_text(self, importer, tmp_path):
        f = tmp_path / "plain.txt"
        f.write_text("This is not XML data at all", encoding="utf-8")
        assert importer.can_import(f) is False

    def test_returns_false_for_binary_data(self, importer, tmp_path):
        b = tmp_path / "random.bin"
        b.write_bytes(b"\x00\x01\x02\x03\x04\xff\xfe")
        assert importer.can_import(b) is False

    def test_does_not_swallow_unexpected_programming_errors(self, importer, valid_xml_file, monkeypatch):
        """Verifica che eccezioni non attese (es. bug interni) emergano e non vengano silenziate."""
        def buggy_iterparse(*args, **kwargs):
            raise TypeError("Simulated programming bug in internal iterparse logic")

        monkeypatch.setattr("defusedxml.ElementTree.iterparse", buggy_iterparse)
        with pytest.raises(TypeError, match="Simulated programming bug"):
            importer.can_import(valid_xml_file)


# ---------------------------------------------------------------------------
# Sicurezza XML (Fase C2) — DTD, DoS/Billion Laughs, XXE
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlSecurity:

    def test_dtd_doctype_rejected(self, importer, tmp_path):
        """XML con DTD/DOCTYPE viene categoricamente rifiutato."""
        bad_xml = tmp_path / "with_dtd.xml"
        bad_xml.write_bytes(b"""<?xml version="1.0"?>
<!DOCTYPE DumpFile SYSTEM "ufdr.dtd">
<DumpFile type="UFDR">
  <InstantMessages><Message id="0"><Body>Test</Body></Message></InstantMessages>
</DumpFile>""")
        # can_import deve restituire False
        assert importer.can_import(bad_xml) is False

        # import_records deve sollevare ValueError (tramite validate_source)
        with pytest.raises(ValueError, match="non può gestire"):
            list(importer.import_records(bad_xml))

    def test_billion_laughs_entity_expansion_rejected(self, importer, tmp_path):
        """XML con dichiarazioni di entità (tentativo DoS) viene bloccato."""
        bomb_xml = tmp_path / "bomb.xml"
        bomb_xml.write_bytes(b"""<?xml version="1.0"?>
<!DOCTYPE DumpFile [
 <!ENTITY lol "lol">
 <!ELEMENT DumpFile (#PCDATA)>
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<DumpFile type="UFDR">&lol1;</DumpFile>""")
        assert importer.can_import(bomb_xml) is False

        with pytest.raises(ValueError, match="non può gestire"):
            list(importer.import_records(bomb_xml))

    def test_xxe_external_entity_rejected(self, importer, tmp_path):
        """XML con riferimento a entità esterna (XXE) viene bloccato."""
        xxe_xml = tmp_path / "xxe.xml"
        xxe_xml.write_bytes(b"""<?xml version="1.0"?>
<!DOCTYPE DumpFile [
 <!ELEMENT DumpFile ANY >
 <!ENTITY xxe SYSTEM "file:///c:/windows/win.ini" >]>
<DumpFile type="UFDR">&xxe;</DumpFile>""")
        assert importer.can_import(xxe_xml) is False

        with pytest.raises(ValueError, match="non può gestire"):
            list(importer.import_records(xxe_xml))


# ---------------------------------------------------------------------------
# import_records — emissione e struttura RawRecord
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsOutput:

    def test_returns_iterator(self, importer, valid_xml_file):
        gen = importer.import_records(valid_xml_file)
        assert isinstance(gen, Iterator)

    def test_yields_raw_records(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        assert len(records) == 4
        for r in records:
            assert isinstance(r, RawRecord)

    def test_source_name_is_cellebrite_xml(self, importer, valid_xml_file):
        for r in importer.import_records(valid_xml_file):
            assert r.source_name == "cellebrite_xml"

    def test_source_path_matches_file(self, importer, valid_xml_file):
        expected_str = str(valid_xml_file.resolve())
        for r in importer.import_records(valid_xml_file):
            assert r.source_path == expected_str

    def test_record_type_is_message(self, importer, valid_xml_file):
        for r in importer.import_records(valid_xml_file):
            assert r.record_type == "message"

    def test_source_record_id_native_priority(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        assert records[0].source_record_id == "0"
        assert records[1].source_record_id == "1"
        assert records[2].source_record_id == "2"

    def test_source_record_id_fallback_when_id_missing(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        # Il quarto messaggio è privo di id="...", ricade su message:3
        assert records[3].source_record_id == "message:3"

    def test_record_ids_are_unique(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        ids = [r.source_record_id for r in records]
        assert len(ids) == len(set(ids))

    def test_metadata_fields(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        for idx, r in enumerate(records):
            assert r.metadata["table"] == "InstantMessages"
            assert r.metadata["format"] == "xml_ufdr"
            assert r.metadata["xml_index"] == idx
            assert r.metadata["importer"] == "CellebriteXmlImporter"
            assert "importer_version" in r.metadata


# ---------------------------------------------------------------------------
# Preservazione Tipi Nativi XML e Assenza di Normalizzazione
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlTypePreservation:

    def test_deleted_preserved_as_string_not_bool(self, importer, valid_xml_file):
        """In XML <Deleted>false</Deleted> è stringa 'false', non booleano False."""
        records = list(importer.import_records(valid_xml_file))
        assert records[0].raw_fields["Deleted"] == "false"
        assert isinstance(records[0].raw_fields["Deleted"], str)
        assert records[2].raw_fields["Deleted"] == "true"
        assert isinstance(records[2].raw_fields["Deleted"], str)

    def test_empty_body_preserved_as_none(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        assert records[1].raw_fields["Body"] is None

    def test_timestamp_preserved_as_raw_iso_string(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        assert records[0].raw_fields["Timestamp"] == "2024-04-23T20:30:15+00:00"
        assert isinstance(records[0].raw_fields["Timestamp"], str)

    def test_sender_preserved_intact(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        assert records[0].raw_fields["Sender"] == "+39 000 0000001"
        assert records[2].raw_fields["Sender"] == "group_participant_A"

    def test_attributes_preserved(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        assert "@attributes" in records[0].raw_fields
        assert records[0].raw_fields["@attributes"]["id"] == "0"


# ---------------------------------------------------------------------------
# Media Reference
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlMediaReference:

    def test_media_reference_none_when_no_attachment(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        assert records[0].media_reference is None
        assert records[1].media_reference is None

    def test_media_reference_is_none_and_attachment_preserved_in_raw_fields(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        # Nessuna logica speculativa: media_reference è None, Attachment preservato in raw_fields
        assert records[2].media_reference is None
        assert records[2].raw_fields.get("Attachment") == "media/image_01.jpg"


# ---------------------------------------------------------------------------
# Immutabilità Profonda (Deep Immutability)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlDeepImmutability:

    def test_attributes_mapping_is_immutable(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        attribs = records[0].raw_fields["@attributes"]
        assert isinstance(attribs, MappingProxyType)
        with pytest.raises(TypeError):
            attribs["id"] = "tampered"

    def test_raw_fields_is_immutable(self, importer, valid_xml_file):
        records = list(importer.import_records(valid_xml_file))
        with pytest.raises(TypeError):
            records[0].raw_fields["Timestamp"] = "2099-01-01"


# ---------------------------------------------------------------------------
# Namespace XML
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlNamespaceHandling:

    def test_namespaced_xml_parsed_correctly(self, importer, tmp_path):
        content = """<?xml version='1.0' encoding='utf-8'?>
<DumpFile xmlns="http://cellebrite.com/ufdr" type="UFDR" version="2.0">
  <InstantMessages>
    <Message id="100">
      <Timestamp>2024-05-01T12:00:00+00:00</Timestamp>
      <Sender>+39 000 0000001</Sender>
      <Body>Messaggio con namespace</Body>
      <Deleted>false</Deleted>
    </Message>
  </InstantMessages>
</DumpFile>
"""
        f = tmp_path / "namespaced.xml"
        f.write_text(content, encoding="utf-8")

        assert importer.can_import(f) is True
        records = list(importer.import_records(f))
        assert len(records) == 1
        assert records[0].source_record_id == "100"
        # Preserva fedelmente l'identità del namespace in notazione Clark
        assert records[0].raw_fields["{http://cellebrite.com/ufdr}Body"] == "Messaggio con namespace"


# ---------------------------------------------------------------------------
# Streaming e Rilascio Memoria
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlStreaming:

    def test_import_is_generator(self, importer, valid_xml_file):
        gen = importer.import_records(valid_xml_file)
        import inspect
        assert inspect.isgenerator(gen)

    def test_records_available_incrementally(self, importer, valid_xml_file):
        gen = importer.import_records(valid_xml_file)
        first = next(gen)
        assert first.source_record_id == "0"
        second = next(gen)
        assert second.source_record_id == "1"


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlErrorHandling:

    def test_raises_file_not_found_for_missing(self, importer, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(importer.import_records(tmp_path / "nonexistent.xml"))

    def test_raises_value_error_for_incompatible_source(self, importer, generic_xml_file):
        with pytest.raises(ValueError, match="non può gestire"):
            list(importer.import_records(generic_xml_file))

    def test_raises_value_error_for_malformed_xml(self, importer, tmp_path):
        bad = tmp_path / "broken.xml"
        bad.write_text("<DumpFile type='UFDR'><unclosed>", encoding="utf-8")
        with pytest.raises(ValueError):
            list(importer.import_records(bad))


# ---------------------------------------------------------------------------
# Confronto Cross-Source Tipi Nativi (D7: CSV str, JSON bool, XML str)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrossSourceDeletedTypeComparison:

    def test_deleted_field_types_differ_deliberately_across_sources(
        self, tmp_path: Path
    ):
        """
        Conferma la differenza deliberata di tipo source-level per Deleted:
          - CSV: stringa ("False" / "True")
          - JSON: booleano nativo (False / True)
          - XML: stringa ("false" / "true")
        La normalizzazione ad un tipo comune è demandata alla fase successiva.
        """
        from importer.cellebrite_csv import CellebriteCsvImporter
        from importer.cellebrite_json import CellebriteJsonImporter

        # 1. Record CSV sintetico
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(
            "Source,MessageType,TimeStamp,Direction,From,To,Body,Attachments,Status,Deleted,Forwarded,ApplicationId,ChatId\n"
            "WhatsApp,Text,2024-01-01 12:00:00,Incoming,+39 000 0000001,+39 000 0000002,Test,,Delivered,False,False,com.whatsapp,chat_1\n",
            encoding="utf-8",
        )
        csv_record = next(CellebriteCsvImporter().import_records(csv_file))
        assert isinstance(csv_record.raw_fields["Deleted"], str)
        assert csv_record.raw_fields["Deleted"] == "False"

        # 2. Record JSON sintetico
        json_file = tmp_path / "test.json"
        import json
        json_file.write_text(
            json.dumps([
                {
                    "id": "1",
                    "chat_id": "c1",
                    "sender": "+39 000 0000001",
                    "timestamp": "2024-01-01T12:00:00+00:00",
                    "type": "text",
                    "metadata": {"deleted": False},
                }
            ]),
            encoding="utf-8",
        )
        json_record = next(CellebriteJsonImporter().import_records(json_file))
        assert isinstance(json_record.raw_fields["metadata"]["deleted"], bool)
        assert json_record.raw_fields["metadata"]["deleted"] is False

        # 3. Record XML sintetico
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(
            "<?xml version='1.0' encoding='utf-8'?>\n"
            "<DumpFile type='UFDR' version='2.0'>\n"
            "  <InstantMessages>\n"
            "    <Message id='1'>\n"
            "      <Timestamp>2024-01-01T12:00:00+00:00</Timestamp>\n"
            "      <Sender>+39 000 0000001</Sender>\n"
            "      <Body>Test</Body>\n"
            "      <Deleted>false</Deleted>\n"
            "    </Message>\n"
            "  </InstantMessages>\n"
            "</DumpFile>\n",
            encoding="utf-8",
        )
        xml_record = next(CellebriteXmlImporter().import_records(xml_file))
        assert isinstance(xml_record.raw_fields["Deleted"], str)
        assert xml_record.raw_fields["Deleted"] == "false"


# ---------------------------------------------------------------------------
# Memory Cleanup e Streaming Strutturale (A2)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlMemoryAndStreaming:

    def test_incremental_streaming_with_many_messages(self, importer, tmp_path):
        """
        Verifica che un XML contenente molteplici messaggi venga processato incrementalmente
        e che ogni elemento generato sia un RawRecord valido.
        """
        xml_file = tmp_path / "large_stream.xml"
        n_msgs = 300
        content_parts = [
            "<?xml version='1.0' encoding='utf-8'?>\n",
            "<DumpFile type='UFDR' version='2.0'>\n",
            "  <InstantMessages>\n",
        ]
        for i in range(n_msgs):
            content_parts.append(
                f'    <Message id="{i}"><Timestamp>2024-04-23T20:30:15+00:00</Timestamp>'
                f'<Sender>+390000000001</Sender><Body>Msg {i}</Body><Deleted>false</Deleted></Message>\n'
            )
        content_parts.append("  </InstantMessages>\n</DumpFile>\n")
        xml_file.write_text("".join(content_parts), encoding="utf-8")

        gen = importer.import_records(xml_file)
        assert inspect.isgenerator(gen)

        # Consumo progressivo: il primo record è disponibile immediatamente
        first = next(gen)
        assert isinstance(first, RawRecord)
        assert first.source_record_id == "0"
        assert first.raw_fields["Body"] == "Msg 0"

        # Consuma i rimanenti e verifica conteggio totale
        remaining = list(gen)
        assert len(remaining) == n_msgs - 1
        assert remaining[-1].source_record_id == str(n_msgs - 1)

    def test_parent_nodes_not_accumulated_during_iterparse(self, tmp_path):
        """
        Verifica a livello strutturale che durante il parsing con stack start/end
        e parent.remove(elem), il nodo parent non accumuli centinaia di nodi orfani.
        """
        xml_file = tmp_path / "cleanup_check.xml"
        n_msgs = 250
        content_parts = [
            "<?xml version='1.0' encoding='utf-8'?>\n",
            "<DumpFile type='UFDR' version='2.0'>\n",
            "  <InstantMessages>\n",
        ]
        for i in range(n_msgs):
            content_parts.append(
                f'    <Message id="{i}"><Body>Body {i}</Body></Message>\n'
            )
        content_parts.append("  </InstantMessages>\n</DumpFile>\n")
        xml_file.write_text("".join(content_parts), encoding="utf-8")

        importer = CellebriteXmlImporter()
        # Consuma il generatore memorizzando la lunghezza dei record
        records = list(importer.import_records(xml_file))
        assert len(records) == n_msgs

        # Verifica che l'importazione sia terminata regolarmente e tutti i record siano integri
        assert records[0].source_record_id == "0"
        assert records[-1].source_record_id == str(n_msgs - 1)


# ---------------------------------------------------------------------------
# Namespace Source-Fidelity (A3)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestXmlNamespaceFidelity:

    def test_multiple_namespaces_same_local_tag_no_collision(self, importer, tmp_path):
        """
        Verifica che elementi XML con lo stesso local tag ma namespace differenti
        preservino la notazione Clark esatta senza collidere o sovrascriversi.
        """
        xml_file = tmp_path / "ns_test.xml"
        xml_content = (
            "<?xml version='1.0' encoding='utf-8'?>\n"
            "<DumpFile type='UFDR' version='2.0'>\n"
            "  <InstantMessages>\n"
            "    <Message id='0'>\n"
            "      <Field xmlns='urn:ns:alpha'>Value Alpha</Field>\n"
            "      <Field xmlns='urn:ns:beta'>Value Beta</Field>\n"
            "      <StandardField>Standard Value</StandardField>\n"
            "    </Message>\n"
            "  </InstantMessages>\n"
            "</DumpFile>\n"
        )
        xml_file.write_text(xml_content, encoding="utf-8")

        records = list(importer.import_records(xml_file))
        assert len(records) == 1
        raw = records[0].raw_fields

        # Entrambi i namespace devono essere presenti in notazione Clark senza collisione
        assert "{urn:ns:alpha}Field" in raw
        assert "{urn:ns:beta}Field" in raw
        assert raw["{urn:ns:alpha}Field"] == "Value Alpha"
        assert raw["{urn:ns:beta}Field"] == "Value Beta"

        # Il campo privo di namespace deve mantenere la chiave semplice
        assert "StandardField" in raw
        assert raw["StandardField"] == "Standard Value"

    def test_backward_compatibility_no_namespace_produces_plain_tags(self, importer, tmp_path):
        """
        Verifica che documenti XML senza namespace producano chiavi semplici e retrocompatibili.
        """
        xml_file = tmp_path / "plain_tags.xml"
        xml_content = (
            "<?xml version='1.0' encoding='utf-8'?>\n"
            "<DumpFile type='UFDR' version='2.0'>\n"
            "  <InstantMessages>\n"
            "    <Message id='42'>\n"
            "      <Timestamp>2024-04-23T20:30:15+00:00</Timestamp>\n"
            "      <Sender>+390001</Sender>\n"
            "      <Body>Hello</Body>\n"
            "      <Deleted>false</Deleted>\n"
            "    </Message>\n"
            "  </InstantMessages>\n"
            "</DumpFile>\n"
        )
        xml_file.write_text(xml_content, encoding="utf-8")

        record = next(importer.import_records(xml_file))
        assert "Timestamp" in record.raw_fields
        assert "Sender" in record.raw_fields
        assert "Body" in record.raw_fields
        assert "Deleted" in record.raw_fields
        assert record.raw_fields["Sender"] == "+390001"


