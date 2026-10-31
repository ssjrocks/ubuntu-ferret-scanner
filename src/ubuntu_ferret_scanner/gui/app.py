"""Creates the Qt application."""
import sys

from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

from ubuntu_ferret_scanner import APP_NAME
from ubuntu_ferret_scanner.gui.main_window import MainWindow


def configure_opengl():
    surface = QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    surface.setDepthBufferSize(24)
    QSurfaceFormat.setDefaultFormat(surface)


def run():
    configure_opengl()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()
