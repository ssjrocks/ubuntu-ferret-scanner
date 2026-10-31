"""The main scanning window."""
import json
import logging
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QProcess
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ubuntu_ferret_scanner import APP_NAME, __version__, appreports, ply, updater
from ubuntu_ferret_scanner.calibration import CLOSE_MODE, LONG_MODE, depth_limits
from ubuntu_ferret_scanner.gui.capture_thread import CaptureThread
from ubuntu_ferret_scanner.gui.settings_dialog import SettingsDialog
from ubuntu_ferret_scanner.gui.view3d import View3D
from ubuntu_ferret_scanner.gui.widgets import CameraTile, DepthGauge
from ubuntu_ferret_scanner.session import ReplaySource, Session
from ubuntu_ferret_scanner.settings import Settings
from ubuntu_ferret_scanner.stream import ScannerSource

LOG = logging.getLogger(__name__)
MODE_LABELS = {"Long range (20-72 cm)": LONG_MODE, "Close range (15-33 cm)": CLOSE_MODE}
STYLE = """
QMainWindow, QWidget { background: #1a1c21; color: #dde; }
QPushButton { background: #2c303a; border: 1px solid #40454f; border-radius: 4px; padding: 7px 14px; }
QPushButton:hover { background: #383d49; }
QPushButton:checked { background: #2d6cdf; border-color: #2d6cdf; }
QPushButton:disabled { color: #667; background: #22242a; }
QPushButton#scan { background: #2a7d46; font-weight: bold; }
QPushButton#scan:checked { background: #c0392b; }
QPushButton#scan:disabled { color: #667; background: #22242a; }
QComboBox { background: #2c303a; border: 1px solid #40454f; padding: 5px 8px; border-radius: 4px; }
QProgressBar { border: 1px solid #40454f; border-radius: 3px; text-align: center; background: #22242a; }
QProgressBar::chunk { background: #2d6cdf; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.capture = None
        self.session = None
        self.builder = None
        self.scanning = False
        self.scan_started = 0.0
        self.has_mesh = False
        self.has_cloud = False
        self.replaying = False
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 900)
        self.setStyleSheet(STYLE)

        self.view = View3D(max(self.settings.live_max_points, 8_000_000))
        self.gauge = DepthGauge()
        self.depth_tile = CameraTile("Depth")
        self.ir_tile = CameraTile("Infrared")
        self.colour_tile = CameraTile("Colour")

        self.connect_button = QPushButton("Connect scanner")
        self.connect_button.setCheckable(True)
        self.connect_button.clicked.connect(self.toggle_connection)
        self.scan_button = QPushButton("Start scan")
        self.scan_button.setObjectName("scan")
        self.scan_button.setCheckable(True)
        self.scan_button.setEnabled(False)
        self.scan_button.clicked.connect(self.toggle_scan)
        self.build_button = QPushButton("Build model")
        self.build_button.setEnabled(False)
        self.build_button.clicked.connect(self.build_model)
        self.mode_box = QComboBox()
        self.mode_box.addItems(MODE_LABELS)
        self.mode_box.setEnabled(False)
        self.mode_box.activated.connect(self.choose_mode)

        self.view_group = QButtonGroup(self)
        view_row = QHBoxLayout()
        for label, mode in (("Point cloud", "points"), ("Bare model", "bare"), ("Textured model", "textured")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked, m=mode: self.view.set_mode(m))
            self.view_group.addButton(button)
            view_row.addWidget(button)
            setattr(self, f"view_{mode}", button)
        self.view_points.setChecked(True)
        self.view_bare.setEnabled(False)
        self.view_textured.setEnabled(False)
        fit = QPushButton("Reset view")
        fit.clicked.connect(self.reset_view)
        view_row.addWidget(fit)
        view_row.addStretch(1)

        self.status = QLabel("Connect the scanner, or open a saved scan.")
        self.link_note = QLabel("")
        self.link_note.setStyleSheet("color: #e0a030;")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress_label = QLabel("")

        controls = QHBoxLayout()
        for widget in (self.connect_button, self.mode_box, self.scan_button, self.build_button):
            controls.addWidget(widget)
        controls.addWidget(self.link_note)
        controls.addStretch(1)
        controls.addWidget(self.progress_label)
        controls.addWidget(self.progress)

        viewer = QHBoxLayout()
        viewer.addWidget(self.gauge)
        viewer.addWidget(self.view, 1)
        tiles = QVBoxLayout()
        for tile in (self.colour_tile, self.ir_tile, self.depth_tile):
            tiles.addWidget(tile, 1)
        tile_box = QWidget()
        tile_box.setLayout(tiles)
        tile_box.setFixedWidth(340)
        viewer.addWidget(tile_box)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addLayout(view_row)
        layout.addLayout(viewer, 1)
        layout.addWidget(self.status)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.build_menus()

    def build_menus(self):
        scan_menu = self.menuBar().addMenu("&File")
        for text, handler in (("Open scan...", self.open_scan), ("Replay scan...", self.replay_scan),
                              ("Open scans folder", self.open_scans_folder), ("Settings...", self.show_settings)):
            action = QAction(text, self)
            action.triggered.connect(handler)
            scan_menu.addAction(action)
        help_menu = self.menuBar().addMenu("&Help")
        for text, handler in (("Report a problem...", self.report_problem), ("Check for updates", self.check_updates)):
            action = QAction(text, self)
            action.triggered.connect(handler)
            help_menu.addAction(action)

    def new_session_folder(self):
        return Path(self.settings.scans_dir) / time.strftime("scan-%Y-%m-%d-%H%M%S")

    def start_capture(self, make_source):
        self.capture = CaptureThread(make_source, self.settings)
        self.capture.update_ready.connect(self.on_update)
        self.capture.colour_ready.connect(self.colour_tile.show_array)
        self.capture.ir_ready.connect(self.ir_tile.show_array)
        self.capture.mode_changed.connect(self.on_mode)
        self.capture.failed.connect(self.on_failed)
        self.capture.source_ended.connect(self.on_source_ended)
        self.capture.scan_stopped.connect(self.on_scan_stopped)
        self.capture.finished.connect(self.on_capture_finished)
        self.capture.start()

    def toggle_connection(self, connected):
        if connected:
            self.status.setText("Connecting. The scanner's projector turns on while connected.")
            self.start_capture(ScannerSource)
            self.scan_button.setEnabled(True)
            self.mode_box.setEnabled(True)
            self.connect_button.setText("Disconnect")
        else:
            self.stop_capture()

    def stop_capture(self):
        if self.capture:
            self.capture.stop()
            self.capture.wait(5000)

    def on_capture_finished(self):
        self.capture = None
        self.connect_button.setChecked(False)
        self.connect_button.setText("Connect scanner")
        self.scan_button.setEnabled(False)
        self.scan_button.setChecked(False)
        self.scan_button.setText("Start scan")
        self.mode_box.setEnabled(False)
        self.scanning = False
        self.replaying = False
        self.gauge.set_distance(None)
        for tile in (self.colour_tile, self.ir_tile, self.depth_tile):
            tile.clear()

    def toggle_scan(self, start):
        if start:
            folder = self.new_session_folder()
            self.session = Session(folder)
            self.view.reset_points()
            self.view.set_mode("points")
            self.view_points.setChecked(True)
            self.capture.send("start_scan", folder)
            self.scanning = True
            self.scan_started = time.monotonic()
            self.scan_button.setText("Stop scan")
            self.mode_box.setEnabled(False)
            self.build_button.setEnabled(False)
        else:
            self.capture.send("stop_scan")

    def on_scan_stopped(self, folder):
        if self.replaying:
            return
        self.session = Session(folder)
        self.scanning = False
        self.scan_button.setChecked(False)
        self.scan_button.setText("Start scan")
        self.mode_box.setEnabled(self.capture is not None and not self.replaying)
        self.build_button.setEnabled(True)
        self.status.setText(f"Scan saved to {folder}. Build a model when you're ready.")

    def choose_mode(self):
        mode = MODE_LABELS[self.mode_box.currentText()]
        if self.capture and not self.scanning:
            self.status.setText("Changing depth range.")
            self.capture.send("set_mode", mode)

    def on_mode(self, mode):
        near, far = depth_limits(mode)
        self.gauge.set_limits(near, far)
        for label, value in MODE_LABELS.items():
            if value == mode:
                self.mode_box.setCurrentText(label)
        if not self.scanning and not self.replaying:
            self.status.setText("Scanner connected. Aim at your subject and watch the distance gauge.")

    def on_update(self, update):
        self.gauge.set_distance(update.distance_mm)
        if update.depth_preview is not None:
            self.depth_tile.show_array(update.depth_preview)
        if update.points is not None and len(update.points):
            self.view.add_points(update.points, update.colours)
        if self.scanning or self.replaying:
            names = {"lost": "Tracking lost: go back to something already scanned", "ok": "Tracking",
                     "first": "Tracking", "idle": "Starting"}
            elapsed = time.monotonic() - self.scan_started
            self.status.setText(
                f"{names.get(update.state, update.state)}   |   {elapsed:.0f} s   |   {update.frames} frames   |   "
                f"{update.total_points:,} points   |   {update.process_ms:.0f} ms per frame   |   "
                f"lost {update.lost_frames}, recording drops {update.extra.get('lost', 0)}"
            )

    def on_failed(self, message):
        LOG.error("capture failed: %s", message)
        short = message.strip().splitlines()[-1]
        self.status.setText(f"Stopped: {short}")
        QMessageBox.warning(self, APP_NAME, short)

    def on_source_ended(self):
        self.status.setText("Replay finished.")
        self.replaying = False

    def replay_scan(self):
        folder = QFileDialog.getExistingDirectory(self, "Replay a saved scan", self.settings.scans_dir)
        if folder:
            self.start_replay(folder)

    def start_replay(self, folder, speed=1.0):
        self.stop_capture()
        self.open_session(folder, load=False)
        scratch = Path(folder) / ".replay"
        self.view.reset_points()
        self.view.set_mode("points")
        self.replaying = True
        self.scan_started = time.monotonic()
        self.start_capture(lambda: ReplaySource(folder, speed))
        self.capture.send("start_scan", scratch)

    def open_scan(self):
        folder = QFileDialog.getExistingDirectory(self, "Open a saved scan", self.settings.scans_dir)
        if folder:
            self.open_session(folder)

    def open_session(self, folder, load=True):
        self.session = Session(folder)
        self.build_button.setEnabled(self.session.has_data())
        if load:
            self.load_results()

    def load_results(self):
        session = self.session
        if session.cloud_path.exists():
            points, colours = ply.read_cloud(session.cloud_path)
            self.view.set_cloud(points, colours)
            self.view_points.setChecked(True)
            self.has_cloud = True
        if session.mesh_path.exists():
            vertices, normals, colours, faces = ply.read_mesh(session.mesh_path)
            self.view.set_mesh(vertices, normals, colours, faces)
            self.view_bare.setEnabled(True)
            self.view_textured.setEnabled(True)
            self.has_mesh = True
        self.status.setText(f"Opened {session.folder}")

    def reset_view(self):
        self.view.yaw, self.view.pitch = 0.0, 0.0
        self.view.update()

    def build_model(self):
        if self.builder or not self.session:
            return
        self.builder = QProcess(self)
        self.builder.readyReadStandardOutput.connect(self.on_build_output)
        self.builder.finished.connect(self.on_build_finished)
        self.builder.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.build_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress_label.setText("Starting build")
        arguments = ["-n", "5", sys.executable, "-m", "ubuntu_ferret_scanner", "build", str(self.session.folder)]
        self.builder.start("nice", arguments)

    def on_build_output(self):
        while self.builder and self.builder.canReadLine():
            line = bytes(self.builder.readLine()).decode().strip()
            try:
                message = json.loads(line)
            except ValueError:
                continue
            self.progress_label.setText(message["stage"])
            progress = message.get("progress")
            if progress is None:
                self.progress.setRange(0, 0)
            else:
                self.progress.setRange(0, 100)
                self.progress.setValue(int(progress * 100))
            if "summary" in message:
                s = message["summary"]
                self.status.setText(
                    f"Built in {s['seconds']:.0f} s: {s['aligned']} frames, {s['points']:,} points, "
                    f"{s['triangles']:,} triangles at {s['voxel_mm']} mm, {s['loops']} loop matches."
                )

    def on_build_finished(self, code, status):
        error = bytes(self.builder.readAllStandardError()).decode()
        self.builder.deleteLater()
        self.builder = None
        self.progress.setVisible(False)
        self.progress_label.setText("")
        self.build_button.setEnabled(True)
        if code != 0:
            last = error.strip().splitlines()[-1] if error.strip() else f"exit code {code}"
            LOG.error("build failed: %s", error)
            appreports.report_error(RuntimeError(last), "model build failed")
            self.status.setText(f"Build failed: {last}")
            QMessageBox.warning(self, APP_NAME, f"The build failed:\n{last}")
            return
        summary = self.status.text()
        self.load_results()
        self.status.setText(summary)

    def show_settings(self):
        SettingsDialog(self.settings, self).exec()

    def open_scans_folder(self):
        folder = Path(self.settings.scans_dir)
        folder.mkdir(parents=True, exist_ok=True)
        QProcess.startDetached("xdg-open", [str(folder)])

    def report_problem(self):
        note, ok = QInputDialog.getMultiLineText(self, "Report a problem", "What went wrong?")
        if ok and note.strip():
            appreports.send_report(note, on_done=lambda sent: LOG.info("report sent: %s", sent))
            self.status.setText("Thanks, the report is being sent.")

    def check_updates(self):
        try:
            update = updater.check()
        except OSError as error:
            QMessageBox.information(self, APP_NAME, f"Couldn't check for updates: {error}")
            return
        text = f"Version {update.version} is available." if update else f"{APP_NAME} {__version__} is up to date."
        QMessageBox.information(self, APP_NAME, text)

    def closeEvent(self, event):
        self.stop_capture()
        if self.builder:
            self.builder.kill()
            self.builder.waitForFinished(3000)
        event.accept()
