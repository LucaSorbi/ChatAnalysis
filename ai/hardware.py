"""
ai/hardware.py
--------------
Rilevamento e audit delle risorse hardware locali per la valutazione scientifica dei modelli LLM.

Principi architetturali (Fase O):
1. NESSUN PRIVILEGIO AMMINISTRATIVO:
   Usa esclusivamente API standard utente (ctypes GlobalMemoryStatusEx, platform, subprocess per CIM).
2. NESSUNA CHIAMATA CLOUD:
   Audit interamente locale e offline.
3. VALUTAZIONE SCIENTIFICA DELLE TAGLIE DI MODELLO:
   Fornisce raccomandazioni obiettive su quantizzazioni e parametri sostenibili.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import platform
import subprocess
import sys
from typing import Any


@dataclass(frozen=True)
class HardwareProfile:
    """
    Profilo hardware rilevato sulla macchina host.
    """
    os_name: str
    architecture: str
    processor: str
    python_version: str
    total_ram_gb: float
    available_ram_gb: float
    gpu_name: str | None
    gpu_adapter_ram_mb: float | None

    def recommended_model_tiers(self) -> dict[str, str]:
        """
        Valutazione scientifica dei parametri e quantizzazioni realisticamente eseguibili su questo host.
        """
        tiers = {}
        # Con RAM disponibile ~5-6 GB e GPU integrata da 1 GB:
        if self.total_ram_gb <= 12.0:
            tiers["1B - 3B (Q4_K_M / Q8_0)"] = (
                "Pienamente sostenibile su CPU/APU locale. Consumo RAM stimato: ~1.5 - 3.0 GB. "
                "Latenza accettabile per elaborazioni peritali e benchmark."
            )
            tiers["7B - 8B (Q4_K_M)"] = (
                "Marginale / ad alto rischio di saturazione RAM. Consumo stimato: ~5.0 - 6.5 GB. "
                "Rischio di thrashing su file di paging durante il processing di contesti ampi."
            )
            tiers["14B - 70B"] = (
                "NON sostenibile su questo host in assenza di GPU discreta dedicata (minimo 12-24 GB VRAM) "
                "o almeno 32-64 GB di RAM di sistema."
            )
        else:
            tiers["7B - 8B (Q4_K_M)"] = "Sostenibile con adeguato margine di memoria volatile."
            tiers["14B+"] = "Richiede GPU dedicata con VRAM proporzionata."

        return tiers


def probe_local_hardware() -> HardwareProfile:
    """
    Rileva le risorse hardware del sistema locale senza richiedere privilegi amministrativi.
    """
    os_name = f"{platform.system()} {platform.release()} ({platform.platform()})"
    arch = platform.machine()
    proc = platform.processor() or "Sconosciuto"
    py_ver = sys.version.split()[0]

    total_ram = 0.0
    avail_ram = 0.0

    # Rilevamento RAM su Windows tramite GlobalMemoryStatusEx
    if platform.system() == "Windows":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        try:
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_ram = round(stat.ullTotalPhys / (1024**3), 2)
                avail_ram = round(stat.ullAvailPhys / (1024**3), 2)
        except Exception:
            pass

    gpu_name: str | None = None
    gpu_ram_mb: float | None = None

    # Rilevamento GPU su Windows senza privilegi
    if platform.system() == "Windows":
        try:
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_VideoController | Select-Object -Property Name, AdapterRAM | ConvertTo-Csv -NoTypeInformation"]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
            if p.returncode == 0:
                lines = [line.strip() for line in p.stdout.strip().splitlines() if line.strip()]
                if len(lines) >= 2:
                    # Prima riga header, seconda riga valori
                    row = lines[1].replace('"', '').split(",")
                    if len(row) >= 2:
                        gpu_name = row[0]
                        try:
                            gpu_ram_mb = round(float(row[1]) / (1024**2), 1)
                        except (ValueError, IndexError):
                            pass
        except Exception:
            pass

    return HardwareProfile(
        os_name=os_name,
        architecture=arch,
        processor=proc,
        python_version=py_ver,
        total_ram_gb=total_ram,
        available_ram_gb=avail_ram,
        gpu_name=gpu_name,
        gpu_adapter_ram_mb=gpu_ram_mb,
    )
