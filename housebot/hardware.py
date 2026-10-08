"""Hardware inspection and LLM model sizing recommendations (zero dependencies).

Inspects system RAM, CPU cores, GPU VRAM (NVIDIA / Apple Silicon Unified Memory),
and provides tailored model recommendations (1B-3B, 7B-8B, 14B, 32B) so local
LLMs run smoothly without exhausting system resources.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class HardwareProfile:
    os_name: str
    arch: str
    cpu_count: int
    total_ram_gb: float
    gpu_name: Optional[str]
    gpu_vram_gb: float
    is_apple_silicon: bool

    def to_dict(self) -> dict:
        return asdict(self)


def get_system_ram_gb() -> float:
    """Return total physical system RAM in gigabytes."""
    try:
        if sys.platform == "linux":
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024 * 1024), 1)
        elif sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()
            return round(int(out) / (1024**3), 1)
        elif sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024**3), 1)
    except Exception:
        pass
    # Fallback to sysconf if available
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round((pages * page_size) / (1024**3), 1)
    except Exception:
        return 8.0


def get_gpu_info() -> Tuple[Optional[str], float]:
    """Detect dedicated NVIDIA GPU name and total VRAM in GB."""
    if shutil.which("nvidia-smi"):
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ]
            out = subprocess.check_output(cmd, encoding="utf-8", timeout=3).strip()
            if out:
                first_line = out.splitlines()[0]
                parts = first_line.split(",")
                if len(parts) >= 2:
                    name = parts[0].strip()
                    mem_mb = float(parts[1].strip())
                    return name, round(mem_mb / 1024, 1)
        except Exception:
            pass
    return None, 0.0


def inspect_hardware() -> HardwareProfile:
    """Probe host OS, CPU, RAM, and GPU to construct a HardwareProfile."""
    os_name = sys.platform
    arch = platform.machine().lower()
    cpu_count = os.cpu_count() or 4
    ram_gb = get_system_ram_gb()
    is_apple_silicon = os_name == "darwin" and arch in ("arm64", "aarch64")
    gpu_name, gpu_vram_gb = get_gpu_info()

    return HardwareProfile(
        os_name=os_name,
        arch=arch,
        cpu_count=cpu_count,
        total_ram_gb=ram_gb,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram_gb,
        is_apple_silicon=is_apple_silicon,
    )


def recommend_model(hw: HardwareProfile) -> dict:
    """Recommend optimal LLM model, tier, and quantization for HouseBot tasks."""
    # Determine effective memory usable for model execution
    if hw.is_apple_silicon:
        # Apple Silicon unified memory can allocate ~70% to Metal / GPU
        eff_mem = hw.total_ram_gb * 0.70
    elif hw.gpu_vram_gb >= 4.0:
        eff_mem = hw.gpu_vram_gb
    else:
        # CPU-only RAM: reserve 3.5 GB for OS + other apps
        eff_mem = max(1.0, hw.total_ram_gb - 3.5)

    if eff_mem >= 20.0:
        return {
            "tier": "High-End",
            "size": "32B",
            "recommended_model": "qwen2.5:32b",
            "fallback_model": "llama3.1:8b",
            "quantization": "Q4_K_M",
            "ram_needed": "~18 GB",
            "description": (
                "Near GPT-4 reasoning and nuanced conversational intelligence with "
                "fast GPU acceleration."
            ),
        }
    elif eff_mem >= 9.0:
        return {
            "tier": "Performance",
            "size": "14B",
            "recommended_model": "qwen2.5:14b",
            "fallback_model": "llama3.1:8b",
            "quantization": "Q4_K_M",
            "ram_needed": "~9 GB",
            "description": (
                "Outstanding accuracy on complex natural language queries, multi-step "
                "intents, and document analysis."
            ),
        }
    elif eff_mem >= 4.0:
        return {
            "tier": "Balanced (Recommended)",
            "size": "7B - 8B",
            "recommended_model": "llama3.1:8b",
            "fallback_model": "qwen2.5:7b",
            "quantization": "Q4_K_M",
            "ram_needed": "~5 GB",
            "description": (
                "The sweet spot for home assistants: fast, highly accurate on reminders, "
                "calendar, lists, and chat."
            ),
        }
    else:
        return {
            "tier": "Lightweight",
            "size": "1B - 3B",
            "recommended_model": "llama3.2:3b",
            "fallback_model": "qwen2.5:1.5b",
            "quantization": "Q4_K_M",
            "ram_needed": "~2.2 GB",
            "description": (
                "Ultra-fast CPU execution with minimal memory usage. Ideal for entry-level "
                "hardware and Raspberry Pi."
            ),
        }


def detect_local_llm_services() -> Dict[str, dict]:
    """Check if common local LLM engines (Ollama, LM Studio, llama-server) are running."""
    services = {}

    # 1. Ollama
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            data = json.loads(r.read())
            models = [m["name"] for m in data.get("models", [])]
            services["ollama"] = {
                "active": True,
                "base_url": "http://127.0.0.1:11434",
                "api": "ollama",
                "models": models,
            }
    except Exception:
        services["ollama"] = {"active": False}

    # 2. LM Studio (Default port 1234)
    try:
        req = urllib.request.Request("http://127.0.0.1:1234/v1/models")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            data = json.loads(r.read())
            models = [m["id"] for m in data.get("data", [])]
            services["lmstudio"] = {
                "active": True,
                "base_url": "http://127.0.0.1:1234",
                "api": "openai",
                "models": models,
            }
    except Exception:
        services["lmstudio"] = {"active": False}

    # 3. llama-server (Default port 8080)
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/v1/models")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            data = json.loads(r.read())
            models = [m["id"] for m in data.get("data", [])]
            services["llama_server"] = {
                "active": True,
                "base_url": "http://127.0.0.1:8080",
                "api": "openai",
                "models": models,
            }
    except Exception:
        services["llama_server"] = {"active": False}

    return services
