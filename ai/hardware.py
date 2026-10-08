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


def _parse_meminfo_content(content: str) -> dict[str, int]:
    """
    Effettua il parsing del contenuto testuale di /proc/meminfo.
    Converte tutti i valori numerici in byte.
    """
    result: dict[str, int] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            val = int(parts[0])
        except ValueError:
            continue
        unit = parts[1].lower() if len(parts) > 1 else "kb"
        if unit == "kb":
            multiplier = 1024
        elif unit == "mb":
            multiplier = 1024**2
        elif unit == "gb":
            multiplier = 1024**3
        elif unit == "b":
            multiplier = 1
        else:
            multiplier = 1024
        result[key] = val * multiplier
    return result


def _probe_ram_windows() -> tuple[float, float]:
    """
    Rileva RAM totale e disponibile su Windows tramite GlobalMemoryStatusEx.
    """
    total_ram = 0.0
    avail_ram = 0.0
    try:
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

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total_ram = round(stat.ullTotalPhys / (1024**3), 2)
            avail_ram = round(stat.ullAvailPhys / (1024**3), 2)
    except Exception:
        pass
    return total_ram, avail_ram


def _probe_gpu_windows() -> tuple[str | None, float | None]:
    """
    Rileva GPU su Windows senza privilegi amministrativi tramite PowerShell/CIM.
    """
    gpu_name: str | None = None
    gpu_ram_mb: float | None = None
    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -Property Name, AdapterRAM | ConvertTo-Csv -NoTypeInformation",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=4.0)
        if p.returncode == 0 and p.stdout:
            lines = [line.strip() for line in p.stdout.strip().splitlines() if line.strip()]
            if len(lines) >= 2:
                row = lines[1].replace('"', '').split(",")
                if len(row) >= 2:
                    gpu_name = row[0]
                    try:
                        gpu_ram_mb = round(float(row[1]) / (1024**2), 1)
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass
    return gpu_name, gpu_ram_mb


def _probe_ram_linux(meminfo_path: str = "/proc/meminfo") -> tuple[float, float]:
    """
    Rileva RAM totale e disponibile su Linux senza privilegi amministrativi.
    Sorgente primaria: /proc/meminfo (MemTotal e MemAvailable).
    Fallback per RAM totale: os.sysconf (SC_PHYS_PAGES * SC_PAGE_SIZE).
    Fallback per RAM disponibile: MemFree + Buffers + Cached o SC_AVPHYS_PAGES.
    """
    total_ram = 0.0
    avail_ram = 0.0

    # 1. Lettura /proc/meminfo
    try:
        if os.path.exists(meminfo_path):
            with open(meminfo_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            info = _parse_meminfo_content(content)
            if "MemTotal" in info:
                total_ram = round(info["MemTotal"] / (1024**3), 2)
            if "MemAvailable" in info:
                avail_ram = round(info["MemAvailable"] / (1024**3), 2)
            elif "MemFree" in info:
                free_b = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
                if free_b > 0:
                    avail_ram = round(free_b / (1024**3), 2)
    except Exception:
        pass

    # 2. Fallback tramite os.sysconf
    if total_ram <= 0.0 and hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                total_ram = round((pages * page_size) / (1024**3), 2)
        except (ValueError, OSError, AttributeError):
            pass

    if avail_ram <= 0.0 and hasattr(os, "sysconf"):
        try:
            av_pages = os.sysconf("SC_AVPHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(av_pages, int) and isinstance(page_size, int) and av_pages > 0 and page_size > 0:
                avail_ram = round((av_pages * page_size) / (1024**3), 2)
        except (ValueError, OSError, AttributeError):
            pass

    return total_ram, avail_ram


def _probe_gpu_linux() -> tuple[str | None, float | None]:
    """
    Rileva GPU dedicata NVIDIA tramite nvidia-smi in modalità non privilegiata.
    Se nvidia-smi non è disponibile o fallisce, degrada restituendo (None, None).
    """
    cmd = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
        if p.returncode == 0 and p.stdout:
            lines = [line.strip() for line in p.stdout.strip().splitlines() if line.strip()]
            if lines:
                parts = [part.strip() for part in lines[0].split(",")]
                if len(parts) >= 2:
                    name = parts[0]
                    try:
                        ram_mb = round(float(parts[1]), 1)
                        return name, ram_mb
                    except (ValueError, IndexError):
                        return name, None
                elif len(parts) == 1 and parts[0]:
                    return parts[0], None
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        pass
    except Exception:
        pass

    return None, None


def _probe_ram_darwin() -> tuple[float, float]:
    """
    Rileva RAM totale e disponibile su macOS (Darwin) tramite sysctl/sysconf/vm_stat.
    """
    total_ram = 0.0
    avail_ram = 0.0

    try:
        p = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2.0)
        if p.returncode == 0 and p.stdout.strip().isdigit():
            total_ram = round(int(p.stdout.strip()) / (1024**3), 2)
    except Exception:
        pass

    if total_ram <= 0.0 and hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                total_ram = round((pages * page_size) / (1024**3), 2)
        except (ValueError, OSError, AttributeError):
            pass

    try:
        p = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2.0)
        if p.returncode == 0 and p.stdout:
            lines = p.stdout.splitlines()
            page_size = 4096
            if lines and "page size of" in lines[0]:
                try:
                    page_size = int(lines[0].split("page size of")[1].split()[0])
                except Exception:
                    page_size = 4096
            stats: dict[str, int] = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    val_str = v.strip().rstrip(".")
                    if val_str.isdigit():
                        stats[k.strip()] = int(val_str)
            free_pages = stats.get("Pages free", 0) + stats.get("Pages inactive", 0)
            if free_pages > 0:
                avail_ram = round((free_pages * page_size) / (1024**3), 2)
    except Exception:
        pass

    return total_ram, avail_ram


def _probe_gpu_darwin() -> tuple[str | None, float | None]:
    """
    Rileva GPU su macOS tramite system_profiler SPDisplaysDataType.
    """
    try:
        p = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True, timeout=3.0)
        if p.returncode == 0 and p.stdout:
            for line in p.stdout.splitlines():
                if "Chipset Model:" in line:
                    model = line.split("Chipset Model:", 1)[1].strip()
                    if model:
                        return model, None
    except Exception:
        pass
    return None, None


def probe_local_hardware() -> HardwareProfile:
    """
    Rileva le risorse hardware del sistema locale senza richiedere privilegi amministrativi.
    Supporta Windows, Linux e macOS in modo portabile e offline.
    """
    system = platform.system()
    os_name = f"{system} {platform.release()} ({platform.platform()})"
    arch = platform.machine()
    proc = platform.processor() or "Sconosciuto"
    py_ver = sys.version.split()[0]

    total_ram = 0.0
    avail_ram = 0.0
    gpu_name: str | None = None
    gpu_ram_mb: float | None = None

    if system == "Windows":
        total_ram, avail_ram = _probe_ram_windows()
        gpu_name, gpu_ram_mb = _probe_gpu_windows()
        if gpu_name is None:
            gpu_name, gpu_ram_mb = _probe_gpu_linux()
    elif system == "Linux":
        total_ram, avail_ram = _probe_ram_linux()
        gpu_name, gpu_ram_mb = _probe_gpu_linux()
    elif system == "Darwin":
        total_ram, avail_ram = _probe_ram_darwin()
        gpu_name, gpu_ram_mb = _probe_gpu_darwin()
    else:
        # Fallback generico per altri sistemi operativi POSIX
        total_ram, avail_ram = _probe_ram_linux()
        gpu_name, gpu_ram_mb = _probe_gpu_linux()

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

