"""
tests/unit/test_ui_privacy_hardening.py
---------------------------------------
Test unitari e di validazione per la privacy hardening e configurazione Streamlit:
- Esistenza e integrità sintattica di .streamlit/config.toml
- Disabilitazione rigorosa della telemetria (gatherUsageStats = false)
- Binding strettamente locale (address e serverAddress = "127.0.0.1", nessun 0.0.0.0 o LAN)
- Mascheramento degli errori (client.showErrorDetails = "none")
- Preservazione delle protezioni di sicurezza (enableCORS = true, enableXsrfProtection = true)
- Validazione tramite modulo ufficiale streamlit.config.get_option
"""
from __future__ import annotations

import os
from pathlib import Path
import pytest
import toml
import streamlit.config as cfg


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".streamlit" / "config.toml"


@pytest.mark.unit
class TestStreamlitPrivacyHardening:

    def test_config_file_exists(self):
        """Verifica l'esistenza fisica del file .streamlit/config.toml nel repository."""
        assert CONFIG_PATH.is_file(), f"File non trovato: {CONFIG_PATH}"

    def test_config_toml_contents_direct(self):
        """Verifica il contenuto del file TOML tramite parsing diretto senza dipendere dall'ambiente."""
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = toml.load(f)

        # [browser]
        assert "browser" in data
        assert data["browser"].get("gatherUsageStats") is False
        assert data["browser"].get("serverAddress") == "127.0.0.1"

        # [server]
        assert "server" in data
        assert data["server"].get("address") == "127.0.0.1"
        assert data["server"].get("enableCORS") is True
        assert data["server"].get("enableXsrfProtection") is True
        assert data["server"].get("maxUploadSize") == 2048
        assert data["server"].get("maxMessageSize") == 2048

        # [client]
        assert "client" in data
        assert data["client"].get("showErrorDetails") == "none"

    def test_streamlit_runtime_options_effective(self):
        """Verifica che Streamlit carichi effettivamente le opzioni di configurazione locali."""
        # Telemetria disabilitata
        assert cfg.get_option("browser.gatherUsageStats") is False

        # Binding locale 127.0.0.1
        assert cfg.get_option("server.address") == "127.0.0.1"
        assert cfg.get_option("browser.serverAddress") == "127.0.0.1"

        # Sicurezza CORS e XSRF attiva
        assert cfg.get_option("server.enableCORS") is True
        assert cfg.get_option("server.enableXsrfProtection") is True

        # Limiti dimensionali upload e messaggi forensi (2048 MB)
        assert cfg.get_option("server.maxUploadSize") == 2048
        assert cfg.get_option("server.maxMessageSize") == 2048

        # Mascheramento dettagli di errore
        assert cfg.get_option("client.showErrorDetails") == "none"

    def test_no_lan_or_wildcard_binding(self):
        """Garantisce l'assenza categorica di binding su 0.0.0.0 o indirizzi broadcast."""
        server_addr = cfg.get_option("server.address")
        browser_addr = cfg.get_option("browser.serverAddress")

        assert server_addr != "0.0.0.0"
        assert browser_addr != "0.0.0.0"
        assert server_addr == "127.0.0.1"
        assert browser_addr == "127.0.0.1"
