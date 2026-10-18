"""Application palette and Qt style sheet."""

COLORS = {
    "background": "#0f1720",
    "sidebar": "#111a26",
    "panel": "#182330",
    "panel_alt": "#1d2a38",
    "border": "#2a3949",
    "text": "#e7edf5",
    "muted": "#91a2b7",
    "accent": "#2dd4bf",
    "accent_dark": "#143f42",
    "profit": "#34d399",
    "loss": "#ff4d5f",
    "warning": "#f4b942",
    "blue": "#62c9ff",
}


def stylesheet():
    c = COLORS
    return f"""
    * {{
        font-family: "Segoe UI";
        font-size: 12px;
        color: {c["text"]};
    }}
    QMainWindow, QDialog, QWidget#AppRoot {{
        background: {c["background"]};
    }}
    QWidget#Sidebar {{
        background: {c["sidebar"]};
        border-right: 1px solid {c["border"]};
    }}
    QWidget#Card, QFrame#Card {{
        background: {c["panel"]};
        border: 1px solid {c["border"]};
        border-radius: 8px;
    }}
    QLabel#Muted, QLabel.muted {{
        color: {c["muted"]};
    }}
    QLabel#MetricValue {{
        font-size: 21px;
        font-weight: 700;
        color: #ffffff;
    }}
    QLabel#SectionTitle {{
        font-size: 20px;
        font-weight: 700;
    }}
    QPushButton {{
        background: {c["panel_alt"]};
        border: 1px solid {c["border"]};
        border-radius: 6px;
        padding: 8px 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: #263749;
        border-color: #3d5268;
    }}
    QPushButton:pressed {{
        background: {c["accent_dark"]};
    }}
    QPushButton[primary="true"] {{
        background: {c["accent"]};
        color: #071311;
        border-color: {c["accent"]};
    }}
    QPushButton[danger="true"] {{
        color: #ffb6bd;
        border-color: #6a3038;
    }}
    QPushButton[nav="true"] {{
        border: 0;
        border-radius: 6px;
        text-align: left;
        padding: 10px 14px;
        background: transparent;
        color: {c["muted"]};
    }}
    QPushButton[nav="true"]:hover, QPushButton[active="true"] {{
        background: #183641;
        color: {c["accent"]};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit {{
        background: #111c27;
        border: 1px solid {c["border"]};
        border-radius: 6px;
        padding: 7px 9px;
        selection-background-color: {c["accent_dark"]};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QDateEdit:focus {{
        border-color: {c["accent"]};
    }}
    QComboBox QAbstractItemView {{
        background: {c["panel_alt"]};
        border: 1px solid {c["border"]};
        selection-background-color: {c["accent_dark"]};
    }}
    QTableView {{
        background: {c["panel"]};
        alternate-background-color: #1b2836;
        border: 1px solid {c["border"]};
        border-radius: 7px;
        gridline-color: transparent;
        selection-background-color: #28bdb0;
        selection-color: #071311;
        outline: none;
    }}
    QTableView::item {{
        padding: 7px 8px;
        border-bottom: 1px solid #243342;
    }}
    QTableView::item:hover {{
        background: #233646;
    }}
    QHeaderView::section {{
        background: #121d29;
        color: #c8d4e2;
        border: 0;
        border-bottom: 1px solid {c["border"]};
        padding: 9px 8px;
        font-weight: 600;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #3a4b5e;
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0;
        height: 0;
    }}
    QProgressBar {{
        border: 0;
        background: #111c27;
        height: 3px;
    }}
    QProgressBar::chunk {{
        background: {c["accent"]};
    }}
    QToolTip {{
        background: #263646;
        color: white;
        border: 1px solid #50657a;
        padding: 5px;
    }}
    QMenu {{
        background: {c["panel_alt"]};
        border: 1px solid {c["border"]};
        padding: 5px;
    }}
    QMenu::item {{
        padding: 7px 22px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background: {c["accent_dark"]};
    }}
    QTabBar::tab {{
        background: transparent;
        color: {c["muted"]};
        padding: 8px 12px;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{
        color: {c["accent"]};
        border-bottom-color: {c["accent"]};
    }}
    """
