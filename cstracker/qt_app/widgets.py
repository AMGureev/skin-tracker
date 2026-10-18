"""Small reusable dashboard widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import COLORS


class Sparkline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.color = COLORS["accent"]
        self.setMinimumSize(84, 34)
        self.setMaximumHeight(42)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, values, color=None):
        self.values = [float(value) for value in values or []]
        if color:
            self.color = color
        self.update()

    def paintEvent(self, event):
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(self.color), 2.0))
        low, high = min(self.values), max(self.values)
        span = high - low or 1.0
        pad = 3
        width = max(1, self.width() - pad * 2)
        height = max(1, self.height() - pad * 2)
        points = []
        for index, value in enumerate(self.values):
            x = pad + width * index / (len(self.values) - 1)
            y = pad + height * (1.0 - (value - low) / span)
            points.append((x, y))
        for first, second in zip(points, points[1:]):
            painter.drawLine(int(first[0]), int(first[1]), int(second[0]), int(second[1]))


class MetricCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumWidth(190)
        self.setMaximumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)

        top = QHBoxLayout()
        self.icon = QLabel("◇")
        self.icon.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {COLORS['accent']};"
        )
        self.title = QLabel(title)
        self.title.setObjectName("Muted")
        top.addWidget(self.icon)
        top.addWidget(self.title)
        top.addStretch()
        layout.addLayout(top)

        value_row = QHBoxLayout()
        self.value = QLabel("—")
        self.value.setObjectName("MetricValue")
        self.sparkline = Sparkline()
        value_row.addWidget(self.value, 1)
        value_row.addWidget(self.sparkline)
        layout.addLayout(value_row)

        self.subtitle = QLabel(" ")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

    def set_metric(self, value, subtitle="", tone="neutral", spark=None):
        tone_color = {
            "profit": COLORS["profit"],
            "loss": COLORS["loss"],
            "warning": COLORS["warning"],
        }.get(tone, "#ffffff")
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {tone_color};")
        self.subtitle.setText(subtitle or " ")
        self.subtitle.setStyleSheet(
            f"color: {tone_color if tone != 'neutral' else COLORS['muted']};"
        )
        self.sparkline.set_data(spark or [], tone_color)


class InsightCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMaximumHeight(66)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        self.title = QLabel(title)
        self.title.setObjectName("Muted")
        self.value = QLabel("—")
        self.value.setWordWrap(True)
        self.value.setStyleSheet("font-weight: 650;")
        layout.addWidget(self.title)
        layout.addWidget(self.value)

    def set_value(self, value, tone="neutral"):
        color = {
            "profit": COLORS["profit"],
            "loss": COLORS["loss"],
            "warning": COLORS["warning"],
        }.get(tone, COLORS["text"])
        self.value.setText(value)
        self.value.setStyleSheet(f"font-weight: 650; color: {color};")
