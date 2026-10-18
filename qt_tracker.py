"""PySide6 entry point for Skin Tracker."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from cstracker.qt_app.main_window import MainWindow
from cstracker.qt_app.theme import COLORS, stylesheet
from cstracker.repository import PortfolioRepository


def app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def database_path():
    return os.path.join(app_base_dir(), "skins.db")


def main():
    QCoreApplication.setOrganizationName("SkinTracker")
    QCoreApplication.setApplicationName("SkinTracker")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(COLORS["background"]))
    palette.setColor(QPalette.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.Base, QColor(COLORS["panel"]))
    palette.setColor(QPalette.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.Button, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.Highlight, QColor(COLORS["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor("#071311"))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())
    repository = PortfolioRepository(database_path())
    smoke_test = "--smoke-test" in sys.argv
    window = MainWindow(repository, start_network=not smoke_test)
    window.show()
    if smoke_test:
        QTimer.singleShot(700, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
