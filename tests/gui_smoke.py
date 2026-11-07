"""Headless GUI check: feeds synthetic transmissions through the real window.

    QT_QPA_PLATFORM=offscreen python tests/gui_smoke.py
"""

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.excepthook = lambda *a: traceback.print_exception(*a)

import numpy as np  # noqa: E402

from morse_decoder import appreports, gui  # noqa: E402
from morse_decoder.synth import transmission  # noqa: E402

appreports.init = lambda *a, **k: None  # never send test failures as real reports
appreports.send_report = lambda *a, **k: None


class FakeCapture:
    def __init__(self, audio):
        self.audio, self.pos = audio, 0

    def start(self):
        pass

    def read(self):
        chunk = self.audio[self.pos : self.pos + 320]  # 40 ms per tick
        self.pos += 320
        return chunk if len(chunk) else np.zeros(320, dtype=np.float32)

    def stop(self):
        pass


audio = np.concatenate([transmission("CQ DE TALKPOD", 30, 15), transmission("TEST 73", 20, 15, seed=2)])
gui.AudioCapture = lambda target=None: FakeCapture(audio)
from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
gui.QSettings = lambda *a: __import__("PyQt6.QtCore", fromlist=["QSettings"]).QSettings(
    tempfile.mktemp(suffix=".ini"), __import__("PyQt6.QtCore", fromlist=["QSettings"]).QSettings.Format.IniFormat
)
win = gui.MainWindow(Path(tempfile.mkdtemp()))
win.timer.setInterval(1)
win.show()
QTimer.singleShot(8000, app.quit)
app.exec()
text = win.history.toPlainText()
print(text)
win.grab().save(sys.argv[1] if len(sys.argv) > 1 else "/dev/null", "PNG")
ok = "CQ DE TALKPOD" in text and "TEST 73" in text
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
