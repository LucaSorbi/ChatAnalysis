"""
tests/unit/test_ai_hardware.py
------------------------------
Test unitari per il rilevamento e audit delle risorse hardware locali (ai/hardware.py):
- Parsing robusto di /proc/meminfo con diverse unità (kB, MB, GB, byte)
- Fallback memoria per Linux (MemAvailable assente, fallback os.sysconf)
- Rilevamento GPU NVIDIA via nvidia-smi (disponibile, assente, timeout, errori)
- Rilevamento multipiattaforma completo (Linux, Windows, macOS/Darwin) tramite mock
- Tolleranza ai guasti e assenza di crash in condizioni anomale
"""
from dataclasses import is_dataclass
import os
import subprocess
from unittest import mock
import pytest

from ai.hardware import (
    HardwareProfile,
    _parse_meminfo_content,
    _probe_gpu_darwin,
    _probe_gpu_linux,
    _probe_gpu_windows,
    _probe_ram_darwin,
    _probe_ram_linux,
    _probe_ram_windows,
    probe_local_hardware,
)


SAMPLE_UBUNTU_MEMINFO = """MemTotal:       32724256 kB
MemFree:         8528464 kB
MemAvailable:   28180456 kB
Buffers:          114428 kB
Cached:         20143376 kB
SwapCached:            0 kB
Active:         12030104 kB
Inactive:        9850116 kB
"""

SAMPLE_LEGACY_LINUX_MEMINFO = """MemTotal:       16777216 kB
MemFree:         2097152 kB
Buffers:         1048576 kB
Cached:          4194304 kB
"""


@pytest.mark.unit
class TestAiHardwareProbe:

    def test_parse_meminfo_content_standard(self):
        info = _parse_meminfo_content(SAMPLE_UBUNTU_MEMINFO)
        assert "MemTotal" in info
        assert "MemAvailable" in info
        assert info["MemTotal"] == 32724256 * 1024
        assert info["MemAvailable"] == 28180456 * 1024

    def test_parse_meminfo_content_various_units_and_malformed(self):
        content = """
        MemTotal: 1048576 kB
        MemFree: 512 MB
        Buffers: 1 GB
        Cached: 1024 B
        InvalidLineWithoutColon
        EmptyVal:
        NonNumeric: abc kB
        UnknownUnit: 2048 xyz
        """
        info = _parse_meminfo_content(content)
        assert info["MemTotal"] == 1048576 * 1024
        assert info["MemFree"] == 512 * (1024**2)
        assert info["Buffers"] == 1 * (1024**3)
        assert info["Cached"] == 1024
        assert info["UnknownUnit"] == 2048 * 1024
        assert "InvalidLineWithoutColon" not in info
        assert "NonNumeric" not in info

    def test_parse_meminfo_empty_or_corrupt_returns_empty_dict(self):
        assert _parse_meminfo_content("") == {}
        assert _parse_meminfo_content("just random text\nno valid rows") == {}

    def test_probe_ram_linux_with_valid_meminfo(self, tmp_path):
        meminfo_file = tmp_path / "meminfo"
        meminfo_file.write_text(SAMPLE_UBUNTU_MEMINFO, encoding="utf-8")

        total_gb, avail_gb = _probe_ram_linux(str(meminfo_file))
        assert total_gb == 31.21
        assert avail_gb == 26.87
        assert total_gb > 0.0
        assert avail_gb > 0.0

    def test_probe_ram_linux_fallback_without_memavailable(self, tmp_path):
        meminfo_file = tmp_path / "meminfo"
        meminfo_file.write_text(SAMPLE_LEGACY_LINUX_MEMINFO, encoding="utf-8")

        total_gb, avail_gb = _probe_ram_linux(str(meminfo_file))
        assert total_gb == 16.0
        # MemFree (2 GB) + Buffers (1 GB) + Cached (4 GB) = 7 GB
        assert avail_gb == 7.0

    def test_probe_ram_linux_fallback_sysconf(self, monkeypatch):
        # Simula assenza di /proc/meminfo e presenza di os.sysconf
        mock_sysconf = mock.MagicMock()
        def fake_sysconf(name):
            if name == "SC_PHYS_PAGES":
                return 8181064
            if name == "SC_PAGE_SIZE":
                return 4096
            if name == "SC_AVPHYS_PAGES":
                return 7045114
            raise ValueError(f"Unknown {name}")

        mock_sysconf.side_effect = fake_sysconf
        monkeypatch.setattr(os, "sysconf", mock_sysconf, raising=False)

        total_gb, avail_gb = _probe_ram_linux("/path/non/existent/meminfo")
        assert total_gb == 31.21
        assert avail_gb == 26.87

    def test_probe_ram_linux_complete_failure_returns_zero(self, monkeypatch):
        # File inesistente e nessun os.sysconf funzionante
        monkeypatch.delattr(os, "sysconf", raising=False)
        total_gb, avail_gb = _probe_ram_linux("/nonexistent/file")
        assert total_gb == 0.0
        assert avail_gb == 0.0

    def test_probe_gpu_linux_nvidia_smi_success(self, monkeypatch):
        fake_stdout = "NVIDIA GeForce GTX TITAN X, 12288\n"
        mock_run = mock.MagicMock(return_value=subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout=fake_stdout,
            stderr="",
        ))
        monkeypatch.setattr(subprocess, "run", mock_run)

        name, ram_mb = _probe_gpu_linux()
        assert name == "NVIDIA GeForce GTX TITAN X"
        assert ram_mb == 12288.0

        # Verifica parametri del comando
        mock_run.assert_called_once()
        cmd_called = mock_run.call_args[0][0]
        assert "nvidia-smi" in cmd_called
        assert "--query-gpu=name,memory.total" in cmd_called
        assert mock_run.call_args[1].get("timeout") == 3.0

    def test_probe_gpu_linux_nvidia_smi_not_found(self, monkeypatch):
        mock_run = mock.MagicMock(side_effect=FileNotFoundError("nvidia-smi not found"))
        monkeypatch.setattr(subprocess, "run", mock_run)

        name, ram_mb = _probe_gpu_linux()
        assert name is None
        assert ram_mb is None

    def test_probe_gpu_linux_nvidia_smi_timeout(self, monkeypatch):
        mock_run = mock.MagicMock(side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3.0))
        monkeypatch.setattr(subprocess, "run", mock_run)

        name, ram_mb = _probe_gpu_linux()
        assert name is None
        assert ram_mb is None

    def test_probe_gpu_linux_nvidia_smi_error_code(self, monkeypatch):
        mock_run = mock.MagicMock(return_value=subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=1,
            stdout="",
            stderr="NVIDIA-SMI has failed",
        ))
        monkeypatch.setattr(subprocess, "run", mock_run)

        name, ram_mb = _probe_gpu_linux()
        assert name is None
        assert ram_mb is None

    def test_probe_gpu_linux_nvidia_smi_malformed_csv(self, monkeypatch):
        mock_run = mock.MagicMock(return_value=subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="GeForce RTX 3080, [N/A]\n",
            stderr="",
        ))
        monkeypatch.setattr(subprocess, "run", mock_run)

        name, ram_mb = _probe_gpu_linux()
        assert name == "GeForce RTX 3080"
        assert ram_mb is None

    def test_probe_local_hardware_on_linux_end_to_end_mocked(self, monkeypatch, tmp_path):
        meminfo_file = tmp_path / "meminfo"
        meminfo_file.write_text(SAMPLE_UBUNTU_MEMINFO, encoding="utf-8")

        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("platform.release", lambda: "5.15.0-107-generic")
        monkeypatch.setattr("platform.platform", lambda: "Linux-5.15.0-107-generic-x86_64")
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        monkeypatch.setattr("platform.processor", lambda: "x86_64")

        # Mock della funzione RAM Linux specifica per puntare al meminfo temporaneo
        monkeypatch.setattr("ai.hardware._probe_ram_linux", lambda: _probe_ram_linux(str(meminfo_file)))

        # Mock di nvidia-smi
        fake_gpu = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="NVIDIA GeForce GTX TITAN X, 12288\n",
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", mock.MagicMock(return_value=fake_gpu))

        profile = probe_local_hardware()
        assert isinstance(profile, HardwareProfile)
        assert profile.os_name.startswith("Linux")
        assert profile.architecture == "x86_64"
        assert profile.total_ram_gb == 31.21
        assert profile.available_ram_gb == 26.87
        assert profile.gpu_name == "NVIDIA GeForce GTX TITAN X"
        assert profile.gpu_adapter_ram_mb == 12288.0

        tiers = profile.recommended_model_tiers()
        assert "7B - 8B (Q4_K_M)" in tiers
        assert "14B+" in tiers

    def test_probe_local_hardware_on_linux_without_gpu_mocked(self, monkeypatch, tmp_path):
        meminfo_file = tmp_path / "meminfo"
        meminfo_file.write_text(SAMPLE_UBUNTU_MEMINFO, encoding="utf-8")

        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("ai.hardware._probe_ram_linux", lambda: _probe_ram_linux(str(meminfo_file)))
        monkeypatch.setattr(subprocess, "run", mock.MagicMock(side_effect=FileNotFoundError("No nvidia-smi")))

        profile = probe_local_hardware()
        assert profile.total_ram_gb == 31.21
        assert profile.available_ram_gb == 26.87
        assert profile.gpu_name is None
        assert profile.gpu_adapter_ram_mb is None

    def test_probe_local_hardware_on_darwin_mocked(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.release", lambda: "23.4.0")
        monkeypatch.setattr("platform.platform", lambda: "macOS-14.4.1-arm64-arm-64bit")

        def fake_darwin_run(cmd, **kwargs):
            if cmd == ["sysctl", "-n", "hw.memsize"]:
                return subprocess.CompletedProcess(cmd, 0, "34359738368\n", "")  # 32 GB
            if cmd == ["vm_stat"]:
                vm_out = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 524288.\nPages inactive: 262144.\n"
                return subprocess.CompletedProcess(cmd, 0, vm_out, "")
            if cmd == ["system_profiler", "SPDisplaysDataType"]:
                sp_out = "Graphics/Displays:\n    Chipset Model: Apple M3 Max\n"
                return subprocess.CompletedProcess(cmd, 0, sp_out, "")
            return subprocess.CompletedProcess(cmd, 1, "", "unknown")

        monkeypatch.setattr(subprocess, "run", fake_darwin_run)

        profile = probe_local_hardware()
        assert profile.os_name.startswith("Darwin")
        assert profile.total_ram_gb == 32.0
        assert profile.available_ram_gb > 0.0
        assert profile.gpu_name == "Apple M3 Max"
        assert profile.gpu_adapter_ram_mb is None

    def test_probe_ram_windows_structure(self):
        # Verifica che la funzione non sollevi eccezioni e restituisca una tupla float
        total, avail = _probe_ram_windows()
        assert isinstance(total, float)
        assert isinstance(avail, float)
        if os.name == "nt":
            assert total > 0.0
            assert avail > 0.0

    def test_hardware_profile_immutability(self):
        profile = HardwareProfile(
            os_name="Linux",
            architecture="x86_64",
            processor="x86_64",
            python_version="3.12.4",
            total_ram_gb=31.21,
            available_ram_gb=26.87,
            gpu_name="NVIDIA GeForce GTX TITAN X",
            gpu_adapter_ram_mb=12288.0,
        )
        assert is_dataclass(profile)
        with pytest.raises(Exception):
            profile.total_ram_gb = 64.0  # dataclass(frozen=True)
