"""
tests/integration/test_ai_lmstudio_smoke.py
-------------------------------------------
Smoke test condizionale per verificare l'integrazione con LM Studio reale in locale.

ATTIVAZIONE:
Questo test è contrassegnato con @pytest.mark.smoke ed è escluso dall'esecuzione ordinaria.
Viene eseguito solo se la variabile d'ambiente RUN_LM_STUDIO_SMOKE è impostata a "1":
    RUN_LM_STUDIO_SMOKE=1 pytest -m smoke
"""
import os
import pytest

from ai.discovery import discover_models_on_client
from ai.lmstudio import LmStudioClient
from ai.models import TopicDecision, TopicQuery
from ai.topics import TopicDetectionAnalyzer


@pytest.mark.smoke
def test_lm_studio_live_smoke():
    if os.environ.get("RUN_LM_STUDIO_SMOKE") != "1":
        pytest.skip("Smoke test LM Studio disattivato. Impostare RUN_LM_STUDIO_SMOKE=1 per eseguirlo.")

    client = LmStudioClient(base_url="http://127.0.0.1:1234")
    if not client.is_available():
        pytest.skip("Server locale LM Studio non avviato su http://127.0.0.1:1234.")

    models = client.list_models()
    assert isinstance(models, tuple)

    report = discover_models_on_client(client)
    assert report.server_available is True

    # Se vi è almeno un modello disponibile, esegue una singola inferenza strutturata minima
    if models:
        test_model = models[0]
        client.model_id = test_model
        completion = client.chat_completion(
            messages=[
                {"role": "system", "content": "Sei un assistente peritale. Rispondi con JSON {\\\"status\\\": \\\"ok\\\"}."},
                {"role": "user", "content": "Verifica connessione."},
            ],
            schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
                "additionalProperties": False,
            },
            temperature=0.0,
            timeout_seconds=15.0,
        )
        assert completion.content != ""
        assert "status" in completion.content
