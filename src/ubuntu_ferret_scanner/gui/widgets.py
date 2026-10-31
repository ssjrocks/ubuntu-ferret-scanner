"""Camera tiles and the distance gauge."""
import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

GOOD_NEAR_SHARE = 0.15  # of the working range, kept clear at each end
GOOD_FAR_SHARE = 0.80


class CameraTile(QWidget):
    def __init__(self, title):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        heading = QLabel(title)
        heading.setStyleSheet("color: #aab; font-size: 11px;")
        self.picture = QLabel()
        self.picture.setMinimumSize(240, 150)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setStyleSheet("background: #111318;")
        self.picture.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(heading)
        layout.addWidget(self.picture, 1)

    def show_array(self, array):
        height, width = array.shape[:2]
        data = np.ascontiguousarray(array)
        if array.ndim == 2:
            image = QImage(data.data, width, height, width, QImage.Format.Format_Grayscale8)
        else:
            image = QImage(data.data, width, height, width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(
            self.picture.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
        )
        self.picture.setPixmap(pixmap)

    def clear(self):
        self.picture.clear()


class DepthGauge(QWidget):
    """Shows scanner distance against the working range, and which way to move."""

    def __init__(self):
        super().__init__()
        self.near_mm, self.far_mm = 201.0, 717.0
        self.distance = None
        self.setFixedWidth(120)

    def set_limits(self, near_mm, far_mm):
        self.near_mm, self.far_mm = near_mm, far_mm
        self.update()

    def set_distance(self, distance):
        self.distance = distance
        self.update()

    def good_range(self):
        span = self.far_mm - self.near_mm
        return self.near_mm + span * GOOD_NEAR_SHARE, self.near_mm + span * GOOD_FAR_SHARE

    def advice(self):
        if self.distance is None:
            return "No depth", QColor("#e05555")
        low, high = self.good_range()
        if self.distance < low:
            return "Move back", QColor("#e0a030")
        if self.distance > high:
            return "Move closer", QColor("#e0a030")
        return "Good", QColor("#45c465")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(30, 34, 22, self.height() - 80)
        span = self.far_mm - self.near_mm

        def y_for(mm):
            return bar.bottom() - (mm - self.near_mm) / span * bar.height()

        painter.fillRect(bar, QColor("#e0a030"))
        low, high = self.good_range()
        painter.fillRect(QRectF(bar.left(), y_for(high), bar.width(), y_for(low) - y_for(high)), QColor("#45c465"))

        painter.setPen(QColor("#ccd"))
        painter.setFont(QFont("Sans", 8))
        painter.drawText(QRectF(0, 12, 120, 18), Qt.AlignmentFlag.AlignCenter, f"{self.far_mm / 10:.0f} cm")
        painter.drawText(QRectF(0, bar.bottom() + 2, 120, 18), Qt.AlignmentFlag.AlignCenter,
                         f"{self.near_mm / 10:.0f} cm")

        text, colour = self.advice()
        if self.distance is not None:
            y = y_for(min(max(self.distance, self.near_mm), self.far_mm))
            painter.setPen(QPen(QColor("white"), 3))
            painter.drawLine(int(bar.left()) - 6, int(y), int(bar.right()) + 6, int(y))
            painter.setPen(QColor("white"))
            painter.setFont(QFont("Sans", 9, QFont.Weight.Bold))
            painter.drawText(QRectF(bar.right() + 8, y - 9, 62, 18), Qt.AlignmentFlag.AlignVCenter,
                             f"{self.distance / 10:.0f} cm")
        painter.setPen(colour)
        painter.setFont(QFont("Sans", 10, QFont.Weight.Bold))
        painter.drawText(QRectF(0, self.height() - 26, 120, 22), Qt.AlignmentFlag.AlignCenter, text)
