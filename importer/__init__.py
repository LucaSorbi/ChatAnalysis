"""
importer/__init__.py
Espone le interfacce pubbliche del layer di ingestion.
"""
from importer.models import RawRecord
from importer.base import BaseImporter
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter

__all__ = ["RawRecord", "BaseImporter", "WhatsAppMsgstoreImporter"]
