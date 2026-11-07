"""Main window: source picker, tone spectrum, keying scope, decoded text."""

import collections
import html
import logging
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_ID, APP_NAME, appreports
from .capture import AudioCapture, list_outputs
from .decoder import RADIO_WPM, Message
from .dsp import RATE
from .engine import Engine

log = logging.getLogger(__name__)

SCOPE_MS = 4000
SPEC_LO, SPEC_HI = 200, 1600
SPEEDS = [("Auto (radio speeds)", "radio", 0), ("Auto (any speed)", "any", 0)] + [
    (f"{w} WPM", "fixed", w) for w in RADIO_WPM
]


def pretty_code(code: str) -> str:
    return code.replace(".", "·").replace("-", "−")


def sensitivity_threshold(value: int) -> float:
    """Slider 1..10 to the tone fraction that keys on (5 -> 0.6)."""
    return 0.9 - 0.06 * value


class Spectrum(QWidget):
    """Audio spectrum SPEC_LO..SPEC_HI Hz; click to set the tone frequency."""

    freq_clicked = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(90)
        self.db = None
        self.freqs = None
        self.tone = 700.0
        self.bandwidth = 100.0
        self.setToolTip("Audio spectrum. Click a peak to tune the decoder to it.")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_spectrum(self, freqs, db):
        self.freqs, self.db = freqs, db
        self.update()

    def _x(self, f):
        return (f - SPEC_LO) / (SPEC_HI - SPEC_LO) * self.width()

    def mousePressEvent(self, ev):
        f = SPEC_LO + ev.position().x() / max(self.width(), 1) * (SPEC_HI - SPEC_LO)
        self.freq_clicked.emit(round(f))

    def paintEvent(self, _ev):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        w, h = self.width(), self.height()
        x0, x1 = self._x(self.tone - self.bandwidth / 2), self._x(self.tone + self.bandwidth / 2)
        p.fillRect(int(x0), 0, max(int(x1 - x0), 1), h, QColor(255, 170, 0, 60))
        if self.db is not None:
            lo, hi = -90.0, -10.0
            p.setPen(QPen(pal.text().color(), 1))
            ys = h - (np.clip(self.db, lo, hi) - lo) / (hi - lo) * (h - 14)
            xs = (self.freqs - SPEC_LO) / (SPEC_HI - SPEC_LO) * w
            for i in range(1, len(xs)):
                p.drawLine(int(xs[i - 1]), int(ys[i - 1]), int(xs[i]), int(ys[i]))
        p.setPen(QPen(QColor(255, 140, 0), 1))
        p.drawLine(int(self._x(self.tone)), 0, int(self._x(self.tone)), h)
        p.setPen(pal.placeholderText().color())
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 0.85)
        p.setFont(f)
        for tick in range(400, SPEC_HI, 200):
            p.drawText(int(self._x(tick)) + 2, 11, f"{tick}")
        p.end()


class Scope(QWidget):
    """The last few seconds of tone fraction, thresholds, and keyed state."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(70)
        self.ratio = np.zeros(SCOPE_MS)
        self.keyed = np.zeros(SCOPE_MS, dtype=bool)
        self.on_thr = 0.6
        self.off_thr = 0.33
        self.setToolTip(
            "Tone strength (fraction of the audio that is the tone) over the last "
            "4 s. Shaded where the decoder hears the tone on."
        )

    def push(self, ratio, keyed):
        n = len(ratio)
        if not n:
            return
        n = min(n, SCOPE_MS)
        self.ratio = np.concatenate([self.ratio[n:], ratio[-n:]])
        self.keyed = np.concatenate([self.keyed[n:], keyed[-n:]])
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        w, h = self.width(), self.height()
        px = max(w, 1)
        # Downsample to one column per pixel.
        idx = (np.arange(px) * SCOPE_MS / px).astype(int)
        if SCOPE_MS >= px:
            r = np.maximum.reduceat(self.ratio, idx)
            k = np.logical_or.reduceat(self.keyed, idx)
        else:
            r, k = self.ratio[idx], self.keyed[idx]
        on_col = QColor(60, 170, 90, 110)
        for x in np.flatnonzero(k):
            p.fillRect(int(x), 0, 1, h, on_col)
        scale = lambda v: h - 2 - min(v, 1.2) / 1.2 * (h - 4)  # noqa: E731
        p.setPen(QPen(QColor(255, 140, 0), 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(scale(self.on_thr)), w, int(scale(self.on_thr)))
        p.setPen(QPen(QColor(120, 120, 255), 1, Qt.PenStyle.DotLine))
        p.drawLine(0, int(scale(self.off_thr)), w, int(scale(self.off_thr)))
        p.setPen(QPen(pal.text().color(), 1))
        ys = [scale(v) for v in r]
        for x in range(1, len(ys)):
            p.drawLine(x - 1, int(ys[x - 1]), x, int(ys[x]))
        p.end()


class MainWindow(QMainWindow):
    report_finished = pyqtSignal(bool)

    def __init__(self, state_dir: Path):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.settings = QSettings("ssjrocks", "morse-decoder")
        self.history_file = state_dir / "messages.txt"
        self.engine = Engine()
        self.capture: AudioCapture | None = None
        self.audio_hist = collections.deque(maxlen=RATE * 6 // 400)  # 6 s in 50 ms blocks
        self._spec_avg = None
        self._last_level = -120.0

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        # Source and controls
        self.source_combo = QComboBox()
        self.source_combo.setToolTip("The output to listen to. Default follows your speaker setting.")
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Refresh the list of outputs")
        refresh.clicked.connect(self.fill_sources)
        self.start_btn = QPushButton("Start")
        self.start_btn.setCheckable(True)
        self.start_btn.toggled.connect(self.on_start_toggled)

        self.tone_spin = QSpinBox()
        self.tone_spin.setRange(300, 1500)
        self.tone_spin.setSuffix(" Hz")
        self.tone_spin.setToolTip("Tone frequency. The Talkpod sends 700 Hz.")
        self.tone_spin.valueChanged.connect(self.on_tone_changed)
        find_btn = QPushButton("Find tone")
        find_btn.setToolTip("Tune to the strongest tone of the last 5 seconds")
        find_btn.clicked.connect(self.find_tone)
        reset_btn = QPushButton("700")
        reset_btn.setToolTip("Back to the Talkpod's 700 Hz")
        reset_btn.clicked.connect(lambda: self.tone_spin.setValue(700))

        self.speed_combo = QComboBox()
        for label, _, _ in SPEEDS:
            self.speed_combo.addItem(label)
        self.speed_combo.setToolTip(
            "Auto (radio speeds) snaps to the Talkpod's 5-40 WPM settings. "
            "Pick a WPM if you know it; it settles single letters like E or T."
        )
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)

        self.sens_slider = QSlider(Qt.Orientation.Horizontal)
        self.sens_slider.setRange(1, 10)
        self.sens_slider.setFixedWidth(110)
        self.sens_slider.setToolTip(
            "Sensitivity. Raise it for weak signals, lower it if noise makes false dits."
        )
        self.sens_slider.valueChanged.connect(self.on_sens_changed)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Listen to"))
        row1.addWidget(self.source_combo, 1)
        row1.addWidget(refresh)
        row1.addWidget(self.start_btn)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Tone"))
        row2.addWidget(self.tone_spin)
        row2.addWidget(reset_btn)
        row2.addWidget(find_btn)
        row2.addSpacing(12)
        row2.addWidget(QLabel("Speed"))
        row2.addWidget(self.speed_combo)
        row2.addSpacing(12)
        row2.addWidget(QLabel("Sensitivity"))
        row2.addWidget(self.sens_slider)
        row2.addStretch(1)

        self.spectrum = Spectrum()
        self.spectrum.freq_clicked.connect(self.tone_spin.setValue)
        self.scope = Scope()

        # Current transmission
        self.live_text = QLabel("")
        big = QFont(mono)
        big.setPointSizeF(max(mono.pointSizeF(), 10) * 2.0)
        big.setBold(True)
        self.live_text.setFont(big)
        self.live_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.live_text.setMinimumHeight(int(big.pointSizeF() * 2))
        self.live_code = QLabel("")
        self.live_code.setFont(mono)
        self.live_code.setWordWrap(True)
        self.live_info = QLabel("Press Start, then play the signal.")
        self.live_info.setStyleSheet("color: palette(placeholder-text)")

        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setFont(mono)
        self.history.setPlaceholderText("Decoded messages appear here.")
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.history.clear)
        copy_btn = QPushButton("Copy all")
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(self.history.toPlainText())
        )
        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self.save_history)
        report_btn = QPushButton("Report a problem…")
        report_btn.clicked.connect(self.report_problem)
        btns = QHBoxLayout()
        btns.addWidget(clear_btn)
        btns.addWidget(copy_btn)
        btns.addWidget(save_btn)
        btns.addStretch(1)
        btns.addWidget(report_btn)

        lay = QVBoxLayout()
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addWidget(self.spectrum, 2)
        lay.addWidget(self.scope, 2)
        lay.addWidget(self.live_text)
        lay.addWidget(self.live_code)
        lay.addWidget(self.live_info)
        lay.addWidget(self.history, 5)
        lay.addLayout(btns)
        central = QWidget()
        central.setLayout(lay)
        self.setCentralWidget(central)
        self.level_label = QLabel("")
        self.statusBar().addPermanentWidget(self.level_label)

        self.report_finished.connect(self._on_report_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.tick)

        # Restore settings
        self.fill_sources()
        self.tone_spin.setValue(int(self.settings.value("tone", 700)))
        self.speed_combo.setCurrentIndex(int(self.settings.value("speed", 0)))
        self.sens_slider.setValue(int(self.settings.value("sensitivity", 5)))
        self.on_tone_changed(self.tone_spin.value())
        self.on_speed_changed(self.speed_combo.currentIndex())
        self.on_sens_changed(self.sens_slider.value())
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(820, 680)
        self.start_btn.setChecked(True)

    # ---- source and settings -----------------------------------------

    def fill_sources(self):
        current = self.source_combo.currentData() if self.source_combo.count() else self.settings.value("source", "")
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("Default output (follows your speaker setting)", "")
        for name, desc in list_outputs():
            if name != "morse-decoder":
                self.source_combo.addItem(desc, name)
        i = self.source_combo.findData(current or "")
        self.source_combo.setCurrentIndex(max(i, 0))
        self.source_combo.blockSignals(False)
        try:
            self.source_combo.currentIndexChanged.disconnect(self.on_source_changed)
        except TypeError:
            pass
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)

    def on_source_changed(self, _i):
        self.settings.setValue("source", self.source_combo.currentData() or "")
        if self.start_btn.isChecked():
            self.stop_capture()
            self.start_capture()

    def on_tone_changed(self, f):
        self.engine.detector.set_tone(f)
        self.spectrum.tone = f
        self.spectrum.update()
        self.settings.setValue("tone", f)

    def on_speed_changed(self, i):
        _, mode, wpm = SPEEDS[i]
        self.engine.decoder.mode = mode
        if wpm:
            self.engine.decoder.fixed_wpm = wpm
            self.engine.decoder.last_unit = 1200 // wpm
        self.settings.setValue("speed", i)

    def on_sens_changed(self, v):
        thr = sensitivity_threshold(v)
        self.engine.keyer.set_sensitivity(thr)
        self.scope.on_thr = self.engine.keyer.on_threshold
        self.scope.off_thr = self.engine.keyer.off_threshold
        self.scope.update()
        self.settings.setValue("sensitivity", v)

    def find_tone(self):
        if not self.audio_hist:
            return
        audio = np.concatenate(list(self.audio_hist)[-RATE * 5 // 400 :])
        n = 4096
        if len(audio) < n:
            return
        win = np.hanning(n)
        frames = [audio[i : i + n] * win for i in range(0, len(audio) - n, n // 2)]
        power = np.mean([np.abs(np.fft.rfft(f)) ** 2 for f in frames], axis=0)
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        band = (freqs >= 300) & (freqs <= 1500)
        f = freqs[band][np.argmax(power[band])]
        self.tone_spin.setValue(int(round(f)))
        self.statusBar().showMessage(f"Tuned to {f:.0f} Hz", 4000)

    # ---- capture -------------------------------------------------------

    def on_start_toggled(self, on):
        if on:
            self.start_capture()
        else:
            self.stop_capture()

    def start_capture(self):
        target = self.source_combo.currentData() or None
        self.capture = AudioCapture(target)
        try:
            self.capture.start()
        except RuntimeError as e:
            self.capture = None
            self.start_btn.setChecked(False)
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        log.info("listening to %s", target or "default output")
        self.start_btn.setText("Stop")
        self.statusBar().showMessage(f"Listening to {self.source_combo.currentText()}")
        self.timer.start()

    def stop_capture(self):
        self.timer.stop()
        if self.capture:
            self.capture.stop()
            self.capture = None
        self.start_btn.blockSignals(True)
        self.start_btn.setChecked(False)
        self.start_btn.blockSignals(False)
        self.start_btn.setText("Start")
        self.statusBar().showMessage("Stopped")

    def tick(self):
        try:
            audio = self.capture.read()
        except RuntimeError as e:
            log.warning("capture stopped: %s", e)
            self.stop_capture()
            QMessageBox.warning(self, APP_NAME, f"Listening stopped.\n\n{e}")
            return
        if len(audio):
            self.audio_hist.append(audio)
            for msg in self.engine.feed(audio):
                self.add_message(msg)
            self.scope.push(self.engine.ratio, self.engine.keyed)
            if len(self.engine.level):
                self._last_level = float(np.max(self.engine.level))
            self.update_spectrum()
        self.update_live()

    def update_spectrum(self):
        n = 2048
        recent = np.concatenate(list(self.audio_hist)[-12:]) if self.audio_hist else np.zeros(0)
        if len(recent) < n:
            return
        x = recent[-n:] * np.hanning(n)
        p = np.abs(np.fft.rfft(x)) ** 2 / (n * n / 8)
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        band = (freqs >= SPEC_LO) & (freqs <= SPEC_HI)
        db = 10 * np.log10(p[band] + 1e-12)
        self._spec_avg = db if self._spec_avg is None or len(self._spec_avg) != len(db) else (
            np.maximum(db, self._spec_avg * 0.7 + db * 0.3)
        )
        self.spectrum.set_spectrum(freqs[band], self._spec_avg)
        self.level_label.setText(f"Audio {self._last_level:.0f} dBFS")

    def update_live(self):
        msg = self.engine.current()
        if msg is None:
            return
        self.live_text.setText(msg.text or " ")
        self.live_code.setText(pretty_code(msg.code))
        self.live_info.setText(self._describe(msg) + "  · receiving")

    @staticmethod
    def _describe(msg: Message) -> str:
        fit = msg.fit
        radio = fit.radio_wpm
        speed = f"{radio} WPM" if radio else f"{fit.wpm:.1f} WPM"
        return f"{speed} (unit {fit.unit:.0f} ms)  · timing error {fit.error * 100:.0f}%"

    def add_message(self, msg: Message):
        stamp = time.strftime("%H:%M:%S", time.localtime(msg.start))
        desc = self._describe(msg)
        log.info("decoded a message: %d characters, %s", len(msg.text), desc)
        self.history.appendHtml(
            f"<b>{stamp}</b>&nbsp;&nbsp;{html.escape(msg.text)}"
            f"<br><span style='color:gray'>&nbsp;&nbsp;{html.escape(pretty_code(msg.code))}"
            f"&nbsp;&nbsp;·&nbsp;{html.escape(desc)}</span>"
        )
        self.live_text.setText(msg.text)
        self.live_code.setText(pretty_code(msg.code))
        self.live_info.setText(desc + "  · done")
        try:
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg.start))}\t"
                        f"{msg.text}\t{msg.code}\t{desc}\n")
        except OSError as e:
            log.warning("could not write %s: %s", self.history_file, e)

    def save_history(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save decoded messages", str(Path.home() / "morse.txt"), "Text (*.txt)"
        )
        if path:
            Path(path).write_text(self.history.toPlainText() + "\n", encoding="utf-8")

    # ---- reports -------------------------------------------------------

    def report_problem(self):
        note, ok = QInputDialog.getMultiLineText(
            self,
            "Report a problem",
            "What went wrong? This is sent with the app's log and settings, "
            "never the decoded messages.",
        )
        if ok:
            extra = {
                "tone_hz": str(self.tone_spin.value()),
                "speed": self.speed_combo.currentText(),
                "sensitivity": str(self.sens_slider.value()),
                "source": "default" if not self.source_combo.currentData() else "other output",
                "listening": str(self.capture is not None),
                "audio_dbfs": f"{self._last_level:.0f}",
            }
            appreports.send_report(note, extra, on_done=self.report_finished.emit)

    def _on_report_finished(self, sent: bool):
        if sent:
            QMessageBox.information(self, "Report a problem", "Report sent. Thanks.")
        else:
            QMessageBox.warning(self, "Report a problem", "The report could not be sent.")

    def closeEvent(self, ev):
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop_capture()
        super().closeEvent(ev)


def run_gui(state_dir: Path) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    win = MainWindow(state_dir)
    win.show()
    return app.exec()
