"""Monochrome theme — pure black/white/gray, no accent color.

The whole UI is a single QSS string. Apply with `app.setStyleSheet(QSS)` in
main_window. Keeping it in one place makes it easy to retune later.
"""

BG       = "#0a0a0a"
SURFACE  = "#141414"
SURFACE2 = "#1c1c1c"
BORDER   = "#262626"
BORDER2  = "#333333"
TEXT     = "#f0f0f0"
MUTED    = "#888888"
HOVER    = "#222222"
ACTIVE   = "#2c2c2c"

QSS = f"""
* {{
    color: {TEXT};
    font-family: "SF Pro Text", "Helvetica Neue", "Helvetica", Arial, sans-serif;
    font-size: 13px;
    selection-background-color: #ffffff;
    selection-color: #000000;
}}

QMainWindow, QWidget {{ background: {BG}; }}

QLabel {{ background: transparent; color: {TEXT}; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[h="1"] {{ font-size: 18px; font-weight: 700; }}
QLabel[h="2"] {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
QLabel[h="3"] {{ font-size: 11px; font-weight: 600; color: {MUTED}; letter-spacing: 1px; }}

QFrame[role="card"] {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QFrame[role="divider"] {{
    background: {BORDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

QPushButton {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    color: {TEXT};
    font-weight: 500;
}}
QPushButton:hover  {{ background: {HOVER}; border-color: {BORDER2}; }}
QPushButton:pressed{{ background: {ACTIVE}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; }}

QPushButton[role="primary"] {{
    background: {TEXT};
    color: #000000;
    border: 1px solid {TEXT};
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{ background: #d8d8d8; border-color: #d8d8d8; }}
QPushButton[role="primary"]:disabled {{ background: {BORDER2}; color: {MUTED}; border-color: {BORDER2}; }}

QPushButton[role="ghost"] {{
    background: transparent;
    border: 1px solid {BORDER};
}}

QPushButton[role="tab"] {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    color: {MUTED};
}}
QPushButton[role="tab"]:hover {{ color: {TEXT}; }}
QPushButton[role="tab"][active="true"] {{
    background: {SURFACE2};
    border-color: {TEXT};
    color: {TEXT};
}}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT};
    selection-background-color: #ffffff;
    selection-color: #000000;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {TEXT};
}}

QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {ACTIVE};
    selection-color: {TEXT};
    padding: 4px;
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    width: 14px; height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::sub-page:horizontal {{ background: {TEXT}; border-radius: 2px; }}

QProgressBar {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    color: {TEXT};
    height: 18px;
}}
QProgressBar::chunk {{ background: {TEXT}; border-radius: 5px; }}

QCheckBox, QRadioButton {{ background: transparent; spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER2};
    border-radius: 3px;
    background: {SURFACE2};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {TEXT}; border-color: {TEXT};
}}

QScrollBar:vertical {{
    background: {BG}; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER2}; min-height: 30px; border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER2};
    padding: 6px 8px;
    border-radius: 6px;
}}

QGraphicsView {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; }}

QStatusBar {{ background: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
"""
