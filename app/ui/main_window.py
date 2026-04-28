"""Main window: a single-page layout with all the controls.

Left column: configuration (channel, story, voice, clips folder, captions).
Right column: live logo positioning canvas + render controls.
Bottom: progress + log.

Heavy work runs on a QThread (RenderWorker) so the UI never freezes.
"""
from __future__ import annotations
from pathlib import Path
import sys
import traceback

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QSize
from PyQt6.QtGui import QFont, QFontDatabase, QColor, QPalette, QTextCharFormat, QSyntaxHighlighter, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QPlainTextEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QSlider, QFileDialog, QFrame, QProgressBar, QStatusBar,
    QScrollArea, QSizePolicy, QButtonGroup, QMessageBox, QCheckBox
)

from app.config import (
    RenderSettings, CaptionStyle, LogoConfig, COLOR_PALETTE,
    PROJECT_ROOT, CHANNELS_DIR, CLIPS_DIR_DEFAULT, OUTPUT_DIR, ASSETS_DIR,
    load_settings, save_settings, channel_dirs,
)
from app.ui.theme import QSS
from app.ui.logo_canvas import LogoCanvas
from app.pipeline.orchestrator import render as run_pipeline
from app.pipeline.tts import PRESET_VOICES_EN


# ---- worker thread ----------------------------------------------------------

class RenderWorker(QObject):
    progress = pyqtSignal(str, float)
    done = pyqtSignal(str)               # emits final path
    failed = pyqtSignal(str)             # emits error message

    def __init__(self, settings: RenderSettings):
        super().__init__()
        self._settings = settings

    def run(self):
        try:
            out = run_pipeline(self._settings, progress=lambda m, f: self.progress.emit(m, f))
            self.done.emit(str(out))
        except Exception:
            self.failed.emit(traceback.format_exc())


# ---- color-tag highlighter for the story editor ----------------------------

class StoryHighlighter(QSyntaxHighlighter):
    """Color the {tag}...{/tag} regions in the story editor."""
    def __init__(self, doc):
        super().__init__(doc)

    def highlightBlock(self, text: str):
        import re
        # color each named span
        stack: list[tuple[int, str]] = []          # (start_index, color_name)
        for m in re.finditer(r"\{(/?)([a-zA-Z_][a-zA-Z0-9_]*)\}", text):
            is_close = bool(m.group(1))
            name = m.group(2).lower()
            if is_close:
                if stack:
                    start, color_name = stack.pop()
                    if color_name in COLOR_PALETTE:
                        fmt = QTextCharFormat()
                        fmt.setForeground(QColor(COLOR_PALETTE[color_name]))
                        self.setFormat(start, m.start() - start, fmt)
            else:
                stack.append((m.end(), name))
        # leftover open tags propagate to end
        for start, color_name in stack:
            if color_name in COLOR_PALETTE:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(COLOR_PALETTE[color_name]))
                self.setFormat(start, len(text) - start, fmt)
        # dim the tags themselves
        tag_fmt = QTextCharFormat()
        tag_fmt.setForeground(QColor("#555"))
        for m in re.finditer(r"\{/?[a-zA-Z_][a-zA-Z0-9_]*\}", text):
            self.setFormat(m.start(), m.end() - m.start(), tag_fmt)


# ---- helpers ----------------------------------------------------------------

def card(*widgets, title: str | None = None) -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    if title:
        l = QLabel(title)
        l.setProperty("h", "3")
        l.setText(title.upper())
        lay.addWidget(l)
    for w in widgets:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return frame


def row(*items) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(8)
    for it in items:
        if isinstance(it, QWidget):
            h.addWidget(it)
        elif isinstance(it, int):
            h.addStretch(it)
        else:
            h.addLayout(it)
    return h


def labeled(label: str, widget_or_layout, stretch: int = 1) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(8)
    l = QLabel(label)
    l.setProperty("muted", "true")
    l.setMinimumWidth(110)
    h.addWidget(l)
    if isinstance(widget_or_layout, QWidget):
        h.addWidget(widget_or_layout, stretch)
    else:
        h.addLayout(widget_or_layout, stretch)
    return h


# ---- main window ------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RVG — Reddit Video Generator")
        self.resize(1500, 1000)
        self.settings = load_settings()
        self._thread: QThread | None = None
        self._worker: RenderWorker | None = None

        self._build_ui()
        self._wire()
        self._refresh_from_settings()

    # ---- ui build ----
    def _build_ui(self):
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(20)

        # LEFT: scrollable config column
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        left = QWidget()
        col = QVBoxLayout(left)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)

        title = QLabel("RVG")
        title.setProperty("h", "1")
        sub = QLabel("Reddit-style video generator")
        sub.setProperty("muted", "true")
        col.addWidget(title); col.addWidget(sub)

        # Channel
        self.channel_btns = QButtonGroup(self)
        self.channel_row = QHBoxLayout()
        col.addWidget(card(self.channel_row, title="channel"))

        # Title (Reddit question text)
        self.title_in = QLineEdit()
        self.title_in.setPlaceholderText("e.g. AITA for refusing to switch seats on a flight?")
        col.addWidget(card(self.title_in, title="reddit question (title card text)"))

        # Story
        self.story_in = QPlainTextEdit()
        self.story_in.setPlaceholderText(
            "Paste your story here. Wrap emotional words with color tags:\n"
            "  I got into a {red}car crash{/red} and {green}threw up{/green} everywhere."
        )
        self.story_in.setMinimumHeight(180)
        self._story_hi = StoryHighlighter(self.story_in.document())
        # quick-insert color buttons
        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel("wrap selection:"))
        for name in ("red", "green", "blue", "yellow", "orange", "purple", "pink"):
            b = QPushButton(name)
            b.setProperty("role", "ghost")
            b.setStyleSheet(f"color: {COLOR_PALETTE[name]};")
            b.clicked.connect(lambda _=False, n=name: self._wrap_tag(n))
            tag_row.addWidget(b)
        tag_row.addStretch(1)
        col.addWidget(card(self.story_in, tag_row, title="story"))

        # Voice — Qwen3-TTS preset voices, no reference clip required.
        self.voice_box = QComboBox()
        for label, vid in PRESET_VOICES_EN:
            self.voice_box.addItem(label, vid)
        self.voice_instruct = QLineEdit()
        self.voice_instruct.setPlaceholderText("(optional) say it casually, or with sarcasm…")
        self.silence_margin = QLineEdit()
        self.silence_margin.setText("0.4s")
        self.silence_margin.setToolTip(
            "auto-editor's --margin. Higher = less aggressive silence cutting.\n"
            "0.4s for TTS (default), 0.2s for human narration with hesitations,\n"
            "1s+ to effectively skip silence removal.")
        self.voice_seed = QSpinBox()
        self.voice_seed.setRange(0, 9_999_999)
        self.voice_seed.setValue(1)
        self.voice_seed.setToolTip(
            "PRNG seed for the TTS sampler. Same seed + same text = identical "
            "audio. If a render sounds glitchy, click re-roll to try a "
            "different sequence.")
        self.voice_reroll_btn = QPushButton("re-roll")
        self.voice_reroll_btn.setProperty("role", "ghost")
        self.voice_reroll_btn.setToolTip("Increment the seed for a fresh take.")
        self.voice_reroll_btn.clicked.connect(
            lambda: self.voice_seed.setValue(self.voice_seed.value() + 1))
        col.addWidget(card(
            labeled("voice", self.voice_box),
            labeled("style prompt", self.voice_instruct),
            labeled("silence margin", self.silence_margin),
            labeled("seed", row(self.voice_seed, self.voice_reroll_btn)),
            title="voice (qwen3-tts)"
        ))

        # Clips folder
        self.clips_dir_in = QLineEdit()
        self.clips_dir_btn = QPushButton("browse")
        self.clips_dir_btn.setProperty("role", "ghost")
        self.seg_min = QDoubleSpinBox(); self.seg_min.setRange(1.0, 30.0); self.seg_min.setSingleStep(0.5)
        self.seg_max = QDoubleSpinBox(); self.seg_max.setRange(1.0, 30.0); self.seg_max.setSingleStep(0.5)
        col.addWidget(card(
            labeled("clips folder", row(self.clips_dir_in, self.clips_dir_btn)),
            labeled("seg min (s)", self.seg_min),
            labeled("seg max (s)", self.seg_max),
            title="background footage"
        ))

        # Captions
        self.cap_font = QComboBox()
        for fam in QFontDatabase.families():
            self.cap_font.addItem(fam)
        self.cap_size  = QSpinBox(); self.cap_size.setRange(24, 220)
        self.cap_weight = QComboBox(); self.cap_weight.addItems(["Regular", "Bold", "Black"])
        self.cap_stroke = QSpinBox(); self.cap_stroke.setRange(0, 24)
        self.cap_shadow = QCheckBox("drop shadow"); self.cap_shadow.setChecked(True)
        self.cap_default_color = QLineEdit(); self.cap_default_color.setMaxLength(7)
        self.cap_uppercase = QCheckBox("uppercase"); self.cap_uppercase.setChecked(True)
        col.addWidget(card(
            labeled("font", self.cap_font),
            labeled("size", self.cap_size),
            labeled("weight", self.cap_weight),
            labeled("stroke", self.cap_stroke),
            labeled("default color", self.cap_default_color),
            row(self.cap_uppercase, self.cap_shadow),
            title="captions"
        ))

        # Title-card duration + final settings
        self.tc_duration = QDoubleSpinBox(); self.tc_duration.setRange(1.0, 10.0); self.tc_duration.setSingleStep(0.25)
        self.saturation  = QDoubleSpinBox(); self.saturation.setRange(0.0, 3.0); self.saturation.setSingleStep(0.05)
        self.volume_db   = QDoubleSpinBox(); self.volume_db.setRange(-20.0, 20.0); self.volume_db.setSingleStep(0.5); self.volume_db.setSuffix(" dB")
        self.speed       = QDoubleSpinBox(); self.speed.setRange(0.5, 3.0); self.speed.setSingleStep(0.05)
        col.addWidget(card(
            labeled("title card hold (s)", self.tc_duration),
            labeled("saturation", self.saturation),
            labeled("volume", self.volume_db),
            labeled("speed (×, no pitch keep)", self.speed),
            title="final pass"
        ))

        col.addStretch(1)
        scroll.setWidget(left)

        # RIGHT: logo canvas + render
        right = QWidget()
        rcol = QVBoxLayout(right)
        rcol.setContentsMargins(0, 0, 0, 0)
        rcol.setSpacing(14)

        rcol.addWidget(QLabel("logo / watermark", styleSheet="font-weight:600;"))
        helper = QLabel("drag a PNG into the frame, or click choose. drag to move (snaps to center / thirds), use the slider to resize.")
        helper.setProperty("muted", "true"); helper.setWordWrap(True)
        rcol.addWidget(helper)

        self.logo_canvas = LogoCanvas()
        self.logo_canvas.setMinimumWidth(380)
        rcol.addWidget(self.logo_canvas, 1)

        self.logo_pick = QPushButton("choose PNG…")
        self.logo_pick.setProperty("role", "ghost")
        self.logo_clear = QPushButton("clear")
        self.logo_clear.setProperty("role", "ghost")
        self.logo_width = QSlider(Qt.Orientation.Horizontal); self.logo_width.setRange(40, 1080); self.logo_width.setValue(240)
        self.logo_opacity = QSlider(Qt.Orientation.Horizontal); self.logo_opacity.setRange(0, 100); self.logo_opacity.setValue(100)
        rcol.addWidget(card(
            row(self.logo_pick, self.logo_clear),
            labeled("size", self.logo_width),
            labeled("opacity", self.logo_opacity),
            title="logo controls"
        ))

        # Render
        self.output_name = QLineEdit("output.mp4")
        self.render_btn = QPushButton("render video")
        self.render_btn.setProperty("role", "primary")
        self.render_btn.setMinimumHeight(40)
        self.open_output = QPushButton("open output folder")
        self.open_output.setProperty("role", "ghost")

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)

        rcol.addWidget(card(
            labeled("output filename", self.output_name),
            row(self.render_btn, self.open_output),
            self.progress,
            self.log,
            title="render"
        ))

        outer.addWidget(scroll, 1)
        outer.addWidget(right, 1)
        self.setCentralWidget(central)

        sb = QStatusBar()
        sb.showMessage("ready")
        self.setStatusBar(sb)

    # ---- wiring ----
    def _wire(self):
        self.clips_dir_btn.clicked.connect(self._pick_clips_dir)
        self.logo_pick.clicked.connect(self._pick_logo)
        self.logo_clear.clicked.connect(self._clear_logo)
        self.logo_width.valueChanged.connect(self.logo_canvas.set_width)
        self.logo_opacity.valueChanged.connect(self.logo_canvas.set_opacity_pct)
        self.render_btn.clicked.connect(self._render)
        self.open_output.clicked.connect(self._open_output)

    def _refresh_channel_buttons(self):
        # clear
        while self.channel_row.count():
            item = self.channel_row.takeAt(0)
            w = item.widget()
            if w: w.setParent(None)
        chans = channel_dirs()
        if not chans:
            help_lbl = QLabel("no channels yet — create a folder under ./channels/<name>/ "
                              "and drop a template.mov + channel.json")
            help_lbl.setProperty("muted", "true")
            help_lbl.setWordWrap(True)
            self.channel_row.addWidget(help_lbl)
            return
        for ch in chans:
            b = QPushButton(ch.name)
            b.setCheckable(True)
            b.setProperty("role", "tab")
            b.toggled.connect(lambda on, n=ch.name, btn=b: self._select_channel(on, n, btn))
            self.channel_btns.addButton(b)
            self.channel_row.addWidget(b)
            if self.settings.channel == ch.name:
                b.setChecked(True)
        self.channel_row.addStretch(1)

    def _select_channel(self, on: bool, name: str, btn: QPushButton):
        if on:
            self.settings.channel = name
            for b in self.channel_btns.buttons():
                b.setProperty("active", "true" if b is btn else "false")
                b.style().unpolish(b); b.style().polish(b)

    def _refresh_from_settings(self):
        s = self.settings
        self._refresh_channel_buttons()
        self.title_in.setText(s.title)
        self.story_in.setPlainText(s.story)
        idx = self.voice_box.findData(s.voice)
        if idx >= 0:
            self.voice_box.setCurrentIndex(idx)
        self.voice_instruct.setText(s.voice_instruct)
        self.silence_margin.setText(s.silence_margin)
        self.voice_seed.setValue(int(s.voice_seed))
        self.clips_dir_in.setText(s.clips_dir)
        self.seg_min.setValue(s.seg_min_s)
        self.seg_max.setValue(s.seg_max_s)
        # captions
        c = s.captions
        i = self.cap_font.findText(c.font_family);
        if i >= 0: self.cap_font.setCurrentIndex(i)
        self.cap_size.setValue(c.font_size)
        i = self.cap_weight.findText(c.font_weight);
        if i >= 0: self.cap_weight.setCurrentIndex(i)
        self.cap_stroke.setValue(c.stroke_width)
        self.cap_shadow.setChecked(c.shadow_opacity > 0)
        self.cap_default_color.setText(c.default_color)
        self.cap_uppercase.setChecked(c.uppercase)
        # logo
        self.logo_canvas.apply_state({
            "path": s.logo.path, "x": s.logo.x, "y": s.logo.y,
            "width": s.logo.width, "opacity": s.logo.opacity,
        })
        self.logo_width.setValue(s.logo.width)
        self.logo_opacity.setValue(int(s.logo.opacity * 100))
        # final pass
        self.tc_duration.setValue(s.title_card_duration_s)
        self.saturation.setValue(s.saturation)
        self.volume_db.setValue(s.volume_db)
        self.speed.setValue(s.speed)
        self.output_name.setText(s.output_filename)

    def _gather_settings(self) -> RenderSettings:
        s = RenderSettings()
        s.channel = self.settings.channel
        s.title = self.title_in.text().strip()
        s.story = self.story_in.toPlainText()
        s.voice = self.voice_box.currentData() or "Aiden"
        s.voice_instruct = self.voice_instruct.text().strip()
        s.silence_margin = self.silence_margin.text().strip() or "0.4s"
        s.voice_seed = int(self.voice_seed.value())
        s.clips_dir = self.clips_dir_in.text().strip() or str(CLIPS_DIR_DEFAULT)
        s.seg_min_s = float(self.seg_min.value())
        s.seg_max_s = float(self.seg_max.value())
        s.title_card_duration_s = float(self.tc_duration.value())
        # captions
        c = CaptionStyle()
        c.font_family = self.cap_font.currentText() or "Helvetica"
        c.font_size   = self.cap_size.value()
        c.font_weight = self.cap_weight.currentText()
        c.stroke_width = self.cap_stroke.value()
        c.shadow_opacity = 0.6 if self.cap_shadow.isChecked() else 0.0
        c.default_color = self.cap_default_color.text() or "#ffffff"
        c.uppercase = self.cap_uppercase.isChecked()
        s.captions = c
        # logo
        st = self.logo_canvas.state()
        s.logo = LogoConfig(path=st["path"], x=int(st["x"]), y=int(st["y"]),
                            width=int(st["width"]), opacity=float(st["opacity"]))
        s.saturation = float(self.saturation.value())
        s.volume_db = float(self.volume_db.value())
        s.speed = float(self.speed.value())
        s.output_filename = self.output_name.text().strip() or "output.mp4"
        return s

    # ---- handlers ----
    def _wrap_tag(self, name: str):
        c = self.story_in.textCursor()
        if c.hasSelection():
            sel = c.selectedText()
            c.insertText(f"{{{name}}}{sel}{{/{name}}}")
        else:
            self.story_in.insertPlainText(f"{{{name}}}{{/{name}}}")

    def _pick_clips_dir(self):
        d = QFileDialog.getExistingDirectory(self, "select clips folder",
                                             self.clips_dir_in.text() or str(PROJECT_ROOT))
        if d:
            self.clips_dir_in.setText(d)

    def _pick_logo(self):
        f, _ = QFileDialog.getOpenFileName(self, "choose logo PNG",
                                           str(ASSETS_DIR), "Images (*.png *.webp *.jpg *.jpeg)")
        if f:
            self.logo_canvas.load_logo(f)
            st = self.logo_canvas.state()
            self.logo_width.setValue(st["width"])

    def _clear_logo(self):
        self.logo_canvas._scene.removeItem(self.logo_canvas._logo) if self.logo_canvas._logo else None
        self.logo_canvas._logo = None
        self.logo_canvas._logo_path = ""

    def _open_output(self):
        import subprocess
        subprocess.Popen(["open", str(OUTPUT_DIR)])

    def _append_log(self, msg: str):
        self.log.appendPlainText(msg)

    def _render(self):
        s = self._gather_settings()
        self.settings = s
        try:
            save_settings(s)
        except Exception:
            pass

        # validation
        if not s.story.strip():
            QMessageBox.warning(self, "missing story", "paste a story before rendering.")
            return
        if not s.title.strip():
            QMessageBox.warning(self, "missing title", "enter the Reddit question title.")
            return
        clips = Path(s.clips_dir)
        if not clips.exists() or not list(clips.rglob("*.mp4")) and not list(clips.rglob("*.mov")):
            res = QMessageBox.question(self, "no clips found",
                                       f"no .mp4/.mov files under {clips}. proceed anyway?")
            if res != QMessageBox.StandardButton.Yes:
                return

        self.render_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log.clear()
        self._append_log(f"render starting → {OUTPUT_DIR / s.output_filename}")

        self._thread = QThread(self)
        self._worker = RenderWorker(s)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    def _on_progress(self, msg: str, frac: float):
        self.progress.setValue(int(frac * 1000))
        self._append_log(f"  · {msg}")
        self.statusBar().showMessage(msg)

    def _on_done(self, path: str):
        self._append_log(f"done → {path}")
        self.statusBar().showMessage("done")
        self.render_btn.setEnabled(True)
        if self._thread:
            self._thread.quit(); self._thread.wait()
            self._thread = None

    def _on_failed(self, tb: str):
        self._append_log("FAILED")
        self._append_log(tb)
        self.statusBar().showMessage("render failed")
        self.render_btn.setEnabled(True)
        if self._thread:
            self._thread.quit(); self._thread.wait()
            self._thread = None


def launch():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    # dark palette to match QSS for native widgets that don't honor stylesheets
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#0a0a0a"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#f0f0f0"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#141414"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#f0f0f0"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#1c1c1c"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#f0f0f0"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
    app.setPalette(pal)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())
