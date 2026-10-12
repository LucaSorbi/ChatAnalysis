"""
ai/experiment.py
----------------
CLI Pilot Runner per l'esecuzione controllata di benchmark reali su LM Studio locale.

Principi di sicurezza e vincoli operativi (Fase L):
1. NESSUN AUTO-RUN:
   Richiede opt-in esplicito tramite variabile d'ambiente RUN_LM_STUDIO_BENCHMARK=1
   oppure flag esplicito --force-run da riga di comando.
2. NESSUN DOWNLOAD O INSTALLAZIONE AUTOMATICA:
   Verifica che il modello richiesto sia già presente tra i modelli caricati su LM Studio (list_models).
3. ONE MODEL AT A TIME:
   Esegue il benchmark su un singolo modello per volta per rispettare i vincoli di memoria host (~11 GB RAM).
4. LOOPBACK-ONLY:
   Accetta esclusivamente indirizzi di loopback locale (127.0.0.1, localhost, ::1).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure repository root is in sys.path when script is executed directly
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from ai.backend import (
    AiBackendError,
    AnalysisInputTooLargeError,
    LmStudioUnavailableError,
)
from ai.benchmark import (
    check_metadata_discrepancies,
    run_synthetic_benchmark,
    run_synthetic_discovery_benchmark,
    save_benchmark_report,
    save_json_report,
)
from ai.discovery import infer_model_family
from ai.lmstudio import LmStudioClient
from ai.models import ExperimentModelSpec, ModelFamily


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Esecutore di benchmark empirico su modello LLM locale tramite LM Studio."
    )
    parser.add_argument(
        "--model-id",
        required=True,
        help="Identificativo esatto del modello in LM Studio (es. 'qwen2.5-1.5b-instruct').",
    )
    parser.add_argument(
        "--family",
        required=True,
        choices=[f.value for f in ModelFamily],
        help="Famiglia architetturale del modello (LLAMA, QWEN, DEEPSEEK, OTHER).",
    )
    parser.add_argument(
        "--quantization",
        default=None,
        help="Quantizzazione del modello se nota (es. 'Q4_K_M').",
    )
    parser.add_argument(
        "--parameter-size",
        default=None,
        help="Dimensione stimata parametri (es. '1.5B', '3B').",
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=None,
        help="Lunghezza del contesto di runtime per il benchmark (es. 8192 token).",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:1234",
        help="URL base del server LM Studio (solo loopback locale).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Cartella di destinazione per i file di report JSON e Markdown.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout in secondi per le chiamate HTTP di completamento (default: 120.0).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Numero massimo di token generabili dal modello (default: 512).",
    )
    parser.add_argument(
        "--force-run",
        action="store_true",
        help="Forza l'esecuzione anche se la variabile RUN_LM_STUDIO_BENCHMARK=1 non è impostata.",
    )
    parsed = parser.parse_args(args)
    if parsed.timeout <= 0:
        parser.error("--timeout deve essere strettamente positivo (> 0).")
    if parsed.max_tokens <= 0:
        parser.error("--max-tokens deve essere strettamente positivo (> 0).")
    return parsed


def main(cli_args: list[str] | None = None) -> int:
    args = parse_args(cli_args)

    # 1. Verifica Opt-In Guard (FASE L1)
    is_opt_in = os.environ.get("RUN_LM_STUDIO_BENCHMARK") == "1" or args.force_run
    if not is_opt_in:
        print(
            "ERRORE DI SICUREZZA: Il benchmark reale su LM Studio richiede opt-in esplicito.\n"
            "Imposta la variabile d'ambiente RUN_LM_STUDIO_BENCHMARK=1 oppure passa il flag --force-run.\n"
            "Questo previene esecuzioni accidentali o sovraccarichi della memoria host.",
            file=sys.stderr,
        )
        return 2

    # 2. Inizializzazione Client LM Studio
    try:
        client = LmStudioClient(
            base_url=args.base_url,
            model_id=args.model_id,
        )
    except ValueError as e:
        print(f"ERRORE CONFIGURAZIONE URL: {e}", file=sys.stderr)
        return 1

    # 3. Verifica Disponibilità Server
    print(f"Verifica connessione verso LM Studio su {args.base_url}...")
    if not client.is_available():
        print(
            f"ERRORE: Server LM Studio non disponibile su {args.base_url}.\n"
            "Avvia manualmente LM Studio e avvia il Local Server prima di eseguire il benchmark.",
            file=sys.stderr,
        )
        return 1

    # 4. Verifica Disponibilità del Modello Specificato (FASE L2: nessun download)
    try:
        available_models = client.list_models()
    except AiBackendError as e:
        print(f"ERRORE durante il listing dei modelli su LM Studio: {e}", file=sys.stderr)
        return 1

    if args.model_id not in available_models:
        print(
            f"ERRORE: Il modello specificato '{args.model_id}' non risulta tra i modelli caricati o visibili in LM Studio.\n"
            f"Modelli disponibili sul server: {available_models}\n"
            "Carica prima il modello desiderato all'interno dell'interfaccia di LM Studio.",
            file=sys.stderr,
        )
        return 1

    # 4. Validazione Coerenza Famiglia Modello (SEZIONE G)
    declared_family = ModelFamily(args.family)
    inferred_family = infer_model_family(args.model_id)

    if inferred_family in (ModelFamily.LLAMA, ModelFamily.QWEN, ModelFamily.DEEPSEEK):
        if declared_family != inferred_family:
            print(
                f"ERRORE DI COERENZA MODELLO: Il model_id '{args.model_id}' corrisponde alla famiglia "
                f"'{inferred_family.value}', ma è stato dichiarato '--family {declared_family.value}'.\n"
                f"Per garantire l'integrità scientifica ed evitare report errati, l'esecuzione è bloccata.",
                file=sys.stderr,
            )
            return 1
    elif inferred_family == ModelFamily.OTHER:
        if declared_family != ModelFamily.OTHER:
            print(
                f"ERRORE DI COERENZA MODELLO: Il model_id '{args.model_id}' non consente di verificare "
                f"automaticamente la famiglia dichiarata '{declared_family.value}'. "
                "Utilizzare un identificativo riconoscibile oppure dichiarare '--family OTHER'.",
                file=sys.stderr,
            )
            return 1

    # 5. Distinzione Metadati Dichiarati vs Osservati (SEZIONE H)
    # runtime_context_length: contesto effettivamente configurato ed utilizzato nel benchmark (CLI --context-length)
    # max_context_length: capacità massima supportata dal modello secondo le specifiche osservate dal backend
    declared_metadata = {
        "quantization": args.quantization,
        "parameter_size": args.parameter_size,
        "runtime_context_length": args.context_length,
    }

    observed_metadata: dict[str, Any] = {
        "quantization": None,
        "parameter_size": None,
        "max_context_length": None,
    }

    discrepancies: list[str] = []

    try:
        details_list = client.get_models_detailed()
        for d in details_list:
            if isinstance(d, dict) and (d.get("id") == args.model_id or d.get("key") == args.model_id):
                obs_q = d.get("quantization")
                obs_p = d.get("params_string") or d.get("parameter_size")
                obs_max_c = d.get("max_context_length") or d.get("context_length")
                obs_run_c = d.get("runtime_context_length") or d.get("loaded_context_length")

                observed_metadata["quantization"] = obs_q if obs_q is not None else None
                observed_metadata["parameter_size"] = obs_p if obs_p is not None else None

                if obs_max_c is not None:
                    if isinstance(obs_max_c, (int, float)) and float(obs_max_c).is_integer():
                        observed_metadata["max_context_length"] = int(obs_max_c)
                    elif isinstance(obs_max_c, str) and obs_max_c.strip().isdigit():
                        observed_metadata["max_context_length"] = int(obs_max_c.strip())
                    else:
                        observed_metadata["max_context_length"] = obs_max_c

                if obs_run_c is not None:
                    if isinstance(obs_run_c, (int, float)) and float(obs_run_c).is_integer():
                        observed_metadata["observed_runtime_context_length"] = int(obs_run_c)
                    elif isinstance(obs_run_c, str) and obs_run_c.strip().isdigit():
                        observed_metadata["observed_runtime_context_length"] = int(obs_run_c.strip())
                    else:
                        observed_metadata["observed_runtime_context_length"] = obs_run_c
                break
    except AiBackendError:
        pass

    discrepancies = check_metadata_discrepancies(declared_metadata, observed_metadata)

    if discrepancies:
        print(f"AVVISO DISCREPANZA METADATI: {'; '.join(discrepancies)}", file=sys.stderr)

    spec = ExperimentModelSpec(
        family=declared_family,
        model_id=args.model_id,
        quantization=args.quantization,
        parameter_size=args.parameter_size,
        context_length=args.context_length,
        runtime_context_length=args.context_length,
        max_context_length=observed_metadata.get("max_context_length"),
        metadata={
            "declared_model_metadata": declared_metadata,
            "observed_model_metadata": observed_metadata,
            "metadata_discrepancies": discrepancies,
        },
    )

    print(f"Avvio benchmark controllato per modello: {spec.model_id} (Famiglia: {spec.family.value})")
    print("Esecuzione Topic Detection (DIRECT_MULTILINGUAL vs TRANSLATE_FIRST)...")

    # 6. Esecuzione Benchmark con cattura ristretta alle sole eccezioni operative (SEZIONE F)
    try:
        bench_result = run_synthetic_benchmark(
            client=client,
            model_spec=spec,
            timeout_seconds=args.timeout,
            max_tokens=args.max_tokens,
        )
    except (AiBackendError, AnalysisInputTooLargeError, ValueError) as e:
        print(f"ERRORE durante l'esecuzione del benchmark Topic Detection: {e}", file=sys.stderr)
        return 1

    print("Esecuzione benchmark Open Topic Discovery...")
    try:
        discovery_result = run_synthetic_discovery_benchmark(
            client=client,
            model_spec=spec,
            timeout_seconds=args.timeout,
            max_tokens=args.max_tokens,
        )
    except (AiBackendError, AnalysisInputTooLargeError, ValueError) as e:
        print(f"ERRORE durante l'esecuzione del benchmark Topic Discovery: {e}", file=sys.stderr)
        return 1

    # Salvataggio Report
    out_dir = Path(args.output_dir)
    safe_name = spec.model_id.replace("/", "_").replace(":", "_")
    json_path, md_path = save_benchmark_report(
        bench_result,
        output_dir=out_dir,
        filename_prefix=f"real_benchmark_{safe_name}",
    )

    disc_json = out_dir / f"real_discovery_{safe_name}.json"
    save_json_report(discovery_result, disc_json)

    print("\nBenchmark completato con successo!")
    print(f"Report Detection JSON: {json_path}")
    print(f"Report Detection Markdown: {md_path}")
    print(f"Report Discovery JSON: {disc_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
