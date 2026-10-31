"""The scanner as a frame source, through the native capture library."""
import ctypes
from pathlib import Path

from ubuntu_ferret_scanner.calibration import CLOSE_MODE, LONG_MODE
from ubuntu_ferret_scanner.session import Frame

DEPTH, IR, COLOUR = 0, 1, 2
MAX_FRAME_BYTES = 8 * 1024 * 1024
SUPERSPEED = 4  # libusb's speed code for 5 Gbit/s


class FrameInfo(ctypes.Structure):
    _fields_ = [("host_ms", ctypes.c_double), ("pts", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("number", ctypes.c_uint64)]


class Stats(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("frames", "dropped", "app_skipped", "record_lost", "bad_headers", "failed_transfers")]


def find_library():
    root = Path(__file__).resolve().parent
    candidates = [root / "libferretstream.so",
                  root.parent.parent / "tools/ferret-capture/build/libferretstream.so"]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("libferretstream.so was not found. Build tools/ferret-capture first.")


def load_library():
    lib = ctypes.CDLL(str(find_library()))
    lib.fs_open.restype = ctypes.c_void_p
    lib.fs_open.argtypes = [ctypes.POINTER(ctypes.c_int)]
    lib.fs_enable.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.fs_start.argtypes = [ctypes.c_void_p]
    lib.fs_read.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
                            ctypes.POINTER(FrameInfo), ctypes.c_int]
    lib.fs_record_start.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p]
    lib.fs_record_stop.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.fs_stats.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(Stats)]
    lib.fs_stop.argtypes = [ctypes.c_void_p]
    lib.fs_close.argtypes = [ctypes.c_void_p]
    lib.fs_get_depth_mode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.fs_set_depth_mode.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    return lib


class ScannerSource:
    """Runs depth, IR and colour together, and records depth and colour on request."""

    def __init__(self):
        self.lib = load_library()
        speed = ctypes.c_int(0)
        self.handle = self.lib.fs_open(ctypes.byref(speed))
        if not self.handle:
            raise RuntimeError("Scanner not found. Check that it is plugged in and has USB access.")
        self.fast_link = speed.value >= SUPERSPEED
        self.buffer = ctypes.create_string_buffer(MAX_FRAME_BYTES)
        self.running = False
        self.mode = self.read_mode()

    def read_mode(self):
        name = ctypes.create_string_buffer(64)
        status = self.lib.fs_get_depth_mode(self.handle, name, 64)
        if status != 0:
            raise RuntimeError(f"Could not read the depth mode ({status}).")
        return name.value.decode()

    def set_mode(self, mode):
        if mode not in (CLOSE_MODE, LONG_MODE):
            raise ValueError(mode)
        status = self.lib.fs_set_depth_mode(self.handle, mode.encode())
        if status != 0:
            raise RuntimeError(f"The scanner refused the depth mode ({status}).")
        self.mode = self.read_mode()

    def start_streams(self):
        fps = 30 if self.fast_link else 15
        for camera, name, width, height in ((DEPTH, b"Y14", 1280, 800), (IR, b"Y8", 640, 400),
                                            (COLOUR, b"MJPEG", 1920, 1080)):
            status = self.lib.fs_enable(self.handle, camera, name, width, height, fps)
            if status != 0:
                raise RuntimeError(f"Camera {camera} has no matching mode ({status}).")
        status = self.lib.fs_start(self.handle)
        if status != 0:
            self.lib.fs_stop(self.handle)
            raise RuntimeError(f"The scanner would not start streaming ({status}).")
        self.running = True

    def _read(self, camera, kind, timeout_ms):
        info = FrameInfo()
        size = self.lib.fs_read(self.handle, camera, self.buffer, MAX_FRAME_BYTES, ctypes.byref(info), timeout_ms)
        if size <= 0:
            return None
        return Frame(kind, self.buffer.raw[:size], info.host_ms)

    def next_frames(self, timeout=0.1):
        frames = []
        for camera, kind in ((COLOUR, "colour"), (IR, "ir")):
            frame = self._read(camera, kind, 0)
            if frame:
                frames.append(frame)
        depth = self._read(DEPTH, "depth", int(timeout * 1000))
        if depth:
            frames.append(depth)
        return frames

    def start_recording(self, session):
        for camera, data, index in ((DEPTH, session.depth_path, session.depth_index),
                                    (COLOUR, session.colour_path, session.colour_index)):
            status = self.lib.fs_record_start(self.handle, camera, str(data).encode(), str(index).encode())
            if status != 0:
                raise RuntimeError(f"Could not start recording ({status}).")

    def stop_recording(self):
        self.lib.fs_record_stop(self.handle, DEPTH)
        self.lib.fs_record_stop(self.handle, COLOUR)

    def stats(self):
        values = Stats()
        self.lib.fs_stats(self.handle, DEPTH, ctypes.byref(values))
        return {"dropped": values.dropped + values.app_skipped, "lost": values.record_lost}

    def stop_streams(self):
        if self.running:
            self.lib.fs_stop(self.handle)
            self.running = False

    def close(self):
        self.lib.fs_close(self.handle)
        self.handle = None
