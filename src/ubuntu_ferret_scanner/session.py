"""Scan sessions on disk, and replay of a saved session as if it were the scanner."""
import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ubuntu_ferret_scanner.calibration import DEPTH_BYTES, LONG_MODE


@dataclass
class Frame:
    kind: str  # "depth", "ir" or "colour"
    data: bytes
    host_ms: float


def read_index(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    ms = np.array([float(r["host_ms"]) for r in rows])
    sizes = np.array([int(r["bytes"]) for r in rows], dtype=np.int64)
    return ms, sizes


class Session:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.depth_path = self.folder / "depth.raw"
        self.depth_index = self.folder / "depth.csv"
        self.colour_path = self.folder / "colour.mjpeg"
        self.colour_index = self.folder / "colour.csv"
        self.poses_path = self.folder / "poses.csv"
        self.cloud_path = self.folder / "cloud.ply"
        self.mesh_path = self.folder / "mesh.ply"
        self.stl_path = self.folder / "mesh.stl"
        self.meta_path = self.folder / "session.json"

    def write_meta(self, mode):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps({"format": 1, "mode": mode, "created": time.time()}))

    @property
    def mode(self):
        try:
            return json.loads(self.meta_path.read_text()).get("mode", LONG_MODE)
        except (OSError, ValueError):
            return LONG_MODE

    def has_data(self):
        return self.depth_path.exists() and self.depth_index.exists()


class ColourFrames:
    """Random access to the JPEG frames of a session."""

    def __init__(self, session):
        self.ms, self.sizes = read_index(session.colour_index)
        self.offsets = np.concatenate(([0], np.cumsum(self.sizes)[:-1]))
        self.file = open(session.colour_path, "rb")

    def read(self, index):
        self.file.seek(int(self.offsets[index]))
        return self.file.read(int(self.sizes[index]))

    def nearest(self, host_ms):
        return int(np.argmin(np.abs(self.ms - host_ms)))


class ReplaySource:
    """Feeds a saved session through the live pipeline at its recorded speed."""

    def __init__(self, folder, speed=1.0):
        self.session = Session(folder)
        self.mode = self.session.mode
        self.speed = speed
        self.depth_ms, self.depth_sizes = read_index(self.session.depth_index)
        self.colour = ColourFrames(self.session) if self.session.colour_index.exists() else None
        self.depth_file = open(self.session.depth_path, "rb")
        self.index = 0
        self.colour_index = 0
        self.start = None

    def start_streams(self):
        self.start = time.monotonic()

    def next_frames(self, timeout=0.1):
        """Returns the frames due now, or an empty list. None means the session has ended."""
        if self.index >= len(self.depth_ms):
            return None
        target = self.depth_ms[self.index] - self.depth_ms[0]
        elapsed = (time.monotonic() - self.start) * 1000 * self.speed
        if elapsed < target:
            time.sleep(min((target - elapsed) / 1000 / self.speed, timeout))
            return []

        frames = []
        host_ms = self.depth_ms[self.index]
        if self.colour is not None:
            while self.colour_index < len(self.colour.ms) and self.colour.ms[self.colour_index] <= host_ms:
                frames.append(Frame("colour", self.colour.read(self.colour_index), self.colour.ms[self.colour_index]))
                self.colour_index += 1
        data = self.depth_file.read(DEPTH_BYTES)
        frames.append(Frame("depth", data, host_ms))
        self.index += 1
        return frames

    def start_recording(self, folder):
        pass

    def stop_recording(self):
        pass

    def stats(self):
        return {"dropped": 0, "lost": 0}

    def stop_streams(self):
        pass

    def close(self):
        self.depth_file.close()
