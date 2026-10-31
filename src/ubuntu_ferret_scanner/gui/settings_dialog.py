"""Settings window, with automatic detection of this PC's resources."""
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ubuntu_ferret_scanner.settings import detect_system


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        found = detect_system()
        gpu = found["gpu_name"] or "None found"

        self.use_gpu = QCheckBox("Use the GPU (CUDA) for tracking and building")
        self.use_gpu.setEnabled(found["gpu_name"] is not None)
        self.vram = self.spin(0.5, max(found["vram_gb"], 0.5), 0.5, " GB")
        self.ram = self.spin(1, found["ram_gb"], 1, " GB")
        self.threads = QSpinBox()
        self.threads.setRange(1, found["threads"])
        self.voxel = self.spin(0, 10, 0.25, " mm")
        self.voxel.setSpecialValueText("Finest that fits")
        self.live_voxel = self.spin(1, 10, 0.5, " mm")
        self.live_points = QSpinBox()
        self.live_points.setRange(100_000, 50_000_000)
        self.live_points.setSingleStep(500_000)
        self.stride = QSpinBox()
        self.stride.setRange(1, 10)
        self.folder = QLineEdit()
        browse = QPushButton("Browse")
        browse.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder)
        folder_row.addWidget(browse)
        folder_box = QWidget()
        folder_box.setLayout(folder_row)
        folder_row.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.addRow(QLabel(f"GPU: {gpu}, {found['vram_gb']:.1f} GB. "
                           f"RAM: {found['ram_gb']:.0f} GB. CPU threads: {found['threads']}."))
        form.addRow(self.use_gpu)
        form.addRow("GPU memory limit", self.vram)
        form.addRow("System memory limit", self.ram)
        form.addRow("CPU threads for building", self.threads)
        form.addRow("Model detail (voxel size)", self.voxel)
        form.addRow("Live cloud spacing", self.live_voxel)
        form.addRow("Live cloud point limit", self.live_points)
        form.addRow("Use every Nth frame when building", self.stride)
        form.addRow("Scans folder", folder_box)

        auto = QPushButton("Auto configure")
        auto.clicked.connect(self.auto)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(auto, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.load()

    def spin(self, low, high, step, suffix):
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setSingleStep(step)
        box.setSuffix(suffix)
        return box

    def load(self):
        s = self.settings
        self.use_gpu.setChecked(s.use_gpu)
        self.vram.setValue(s.vram_limit_gb)
        self.ram.setValue(s.ram_limit_gb)
        self.threads.setValue(s.cpu_threads)
        self.voxel.setValue(s.voxel_mm)
        self.live_voxel.setValue(s.live_voxel_mm)
        self.live_points.setValue(s.live_max_points)
        self.stride.setValue(s.build_frame_stride)
        self.folder.setText(s.scans_dir)

    def auto(self):
        self.settings.auto_configure()
        self.load()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Scans folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def save(self):
        s = self.settings
        s.use_gpu = self.use_gpu.isChecked()
        s.vram_limit_gb = self.vram.value()
        s.ram_limit_gb = self.ram.value()
        s.cpu_threads = self.threads.value()
        s.voxel_mm = self.voxel.value()
        s.live_voxel_mm = self.live_voxel.value()
        s.live_max_points = self.live_points.value()
        s.build_frame_stride = self.stride.value()
        s.scans_dir = self.folder.text()
        s.save()
        self.accept()
