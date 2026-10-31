"""User settings and automatic resource limits."""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

RESERVED_VRAM_GB = 3.0  # shared with the desktop and browsers
RESERVED_RAM_GB = 24.0
RESERVED_THREADS = 4


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ubuntu-ferret-scanner" / "settings.json"


@dataclass
class Settings:
    use_gpu: bool = True
    vram_limit_gb: float = 0.0
    ram_limit_gb: float = 0.0
    cpu_threads: int = 0
    voxel_mm: float = 0.0  # 0 picks the finest size the memory limit allows
    live_voxel_mm: float = 3.0
    live_max_points: int = 5_000_000
    build_frame_stride: int = 2
    scans_dir: str = str(Path.home() / "FerretScans")

    @classmethod
    def load(cls):
        settings = cls()
        try:
            stored = json.loads(config_path().read_text())
        except (OSError, ValueError):
            stored = {}
        for key, value in stored.items():
            if key in settings.__dataclass_fields__:
                setattr(settings, key, value)
        if not settings.vram_limit_gb or not settings.ram_limit_gb or not settings.cpu_threads:
            settings.auto_configure(only_missing=True)
        return settings

    def save(self):
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    def auto_configure(self, only_missing=False):
        found = detect_system()
        if not only_missing:
            self.use_gpu = found["gpu_name"] is not None
            self.voxel_mm = 0.0
        vram = max(found["vram_gb"] - RESERVED_VRAM_GB, 0.0)
        ram = max(found["ram_gb"] - RESERVED_RAM_GB, found["ram_gb"] / 2)
        threads = max(found["threads"] - RESERVED_THREADS, 1)
        if not only_missing or not self.vram_limit_gb:
            self.vram_limit_gb = round(vram, 1)
        if not only_missing or not self.ram_limit_gb:
            self.ram_limit_gb = round(ram, 1)
        if not only_missing or not self.cpu_threads:
            self.cpu_threads = threads


def detect_system():
    import torch

    ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    gpu_name, vram_gb = None, 0.0
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    return {"ram_gb": ram_gb, "threads": os.cpu_count() or 1, "gpu_name": gpu_name, "vram_gb": vram_gb}


def device_for(settings):
    import torch

    return torch.device("cuda" if settings.use_gpu and torch.cuda.is_available() else "cpu")
