"""Runs the frame source and live pipeline away from the interface."""
import queue
import time
import traceback

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from ubuntu_ferret_scanner.pipeline import LivePipeline
from ubuntu_ferret_scanner.session import Session
from ubuntu_ferret_scanner.settings import device_for

PREVIEW_INTERVAL = 1 / 15


class CaptureThread(QThread):
    update_ready = pyqtSignal(object)
    colour_ready = pyqtSignal(object)
    ir_ready = pyqtSignal(object)
    mode_changed = pyqtSignal(str)
    failed = pyqtSignal(str)
    source_ended = pyqtSignal()
    scan_stopped = pyqtSignal(object)

    def __init__(self, make_source, settings):
        super().__init__()
        self.make_source = make_source
        self.settings = settings
        self.commands = queue.Queue()
        self.stop_requested = False
        self.session = None

    def send(self, command, value=None):
        self.commands.put((command, value))

    def stop(self):
        self.stop_requested = True

    def handle_commands(self, source, pipeline):
        while True:
            try:
                command, value = self.commands.get_nowait()
            except queue.Empty:
                return
            if command == "start_scan":
                self.session = Session(value)
                self.session.write_meta(pipeline.mode)
                source.start_recording(self.session)
                pipeline.start_scan(self.session)
            elif command == "stop_scan":
                self.finish_scan(source, pipeline)
            elif command == "set_mode":
                self.finish_scan(source, pipeline)
                source.stop_streams()
                source.set_mode(value)
                pipeline.set_mode(source.mode)
                source.start_streams()
                self.mode_changed.emit(source.mode)

    def finish_scan(self, source, pipeline):
        if not pipeline.scanning:
            return
        pipeline.stop_scan()
        source.stop_recording()
        self.scan_stopped.emit(self.session.folder)

    def run(self):
        source = None
        try:
            source = self.make_source()
            pipeline = LivePipeline(device_for(self.settings), self.settings, source.mode)
            self.mode_changed.emit(source.mode)
            source.start_streams()
            last_preview = 0.0
            last_colour_preview = 0.0
            while not self.stop_requested:
                self.handle_commands(source, pipeline)
                frames = source.next_frames()
                if frames is None:
                    self.finish_scan(source, pipeline)
                    self.source_ended.emit()
                    break
                for frame in frames:
                    now = time.monotonic()
                    if frame.kind == "colour":
                        picture = pipeline.add_colour(frame)
                        if picture is not None and now - last_colour_preview >= PREVIEW_INTERVAL:
                            last_colour_preview = now
                            self.colour_ready.emit(picture)
                    elif frame.kind == "ir":
                        self.ir_ready.emit(np.frombuffer(frame.data, dtype=np.uint8).reshape(400, 640))
                    else:
                        want_preview = now - last_preview >= PREVIEW_INTERVAL
                        if want_preview:
                            last_preview = now
                        update = pipeline.process_depth(frame, want_preview)
                        update.extra = source.stats()
                        if want_preview or update.points is not None:
                            self.update_ready.emit(update)
            self.finish_scan(source, pipeline)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if source is not None:
                try:
                    source.stop_streams()
                    source.close()
                except Exception:
                    self.failed.emit(traceback.format_exc())
