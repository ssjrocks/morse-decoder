"""Decode synthetic Talkpod transmissions (docs/morse.md) end to end.

    python -m pytest tests
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from morse_decoder.engine import Engine, decode_audio  # noqa: E402
from morse_decoder.synth import keying, transmission  # noqa: E402

TEXTS = [
    "PARIS",
    "THE QUICK BROWN FOX JUMPS OV",
    "0123456789",
    ".,?'!/()&:;=+-_\"@",
    "HI   THERE  CQ",
    "TT",
]


def expected(text):
    return " ".join(text.split())


@pytest.mark.parametrize("wpm", [5, 15, 30, 35, 40])
@pytest.mark.parametrize("text", TEXTS)
def test_decodes(text, wpm):
    msgs = decode_audio(transmission(text, wpm, snr_db=10))
    assert [m.text for m in msgs] == [expected(text)]
    assert msgs[0].fit.unit == 1200 // wpm


def test_paris_keying_matches_doc():
    # The PARIS example in docs/morse.md, at 30 WPM.
    on, off = (True, 40), (False, 40)
    dah, gap = (True, 120), (False, 120)
    assert keying("PARIS", 30) == [
        on, off, dah, off, dah, off, on, gap,
        on, off, dah, gap,
        on, off, dah, off, on, gap,
        on, off, on, gap,
        on, off, on, off, on,
    ]


@pytest.mark.parametrize("snr", [0, 3])
def test_weak_signal(snr):
    for seed in range(3):
        msgs = decode_audio(transmission("CQ CQ DE TALKPOD TEST 73", 30, snr, seed=seed))
        assert [m.text for m in msgs] == ["CQ CQ DE TALKPOD TEST 73"]


@pytest.mark.parametrize("tone", [670, 730])
def test_off_frequency(tone):
    assert [m.text for m in decode_audio(transmission("PARIS", 30, 15, tone_hz=tone))] == ["PARIS"]


def test_no_false_messages_in_noise():
    from scipy.signal import butter, lfilter, sosfilt

    rng = np.random.default_rng(5)
    n = rng.standard_normal(8000 * 120) * 0.3
    n = sosfilt(butter(4, [250, 3000], btype="band", fs=8000, output="sos"), n)
    a = np.exp(-2 * np.pi * 300 / 8000)
    n = lfilter([1 - a], [1, -a], n) * 4
    assert decode_audio(n.astype(np.float32)) == []


def test_chunking_does_not_matter():
    audio = np.concatenate(
        [transmission("CQ DE TALKPOD", 30, 15), transmission("TEST 73", 20, 15, seed=2)]
    )
    rng = np.random.default_rng(1)
    eng, got, i = Engine(), [], 0
    while i < len(audio):
        k = int(rng.integers(1, 900))
        got += [m.text for m in eng.feed(audio[i : i + k], now=0)]
        i += k
    got += [m.text for m in eng.feed(np.zeros(40000, dtype=np.float32), now=0)]
    assert got == ["CQ DE TALKPOD", "TEST 73"]


def test_fixed_speed_settles_ambiguous_letters():
    # A lone "5" at 5 WPM has the same timing as "TTTTT" at 15 WPM.
    eng = Engine()
    eng.decoder.mode, eng.decoder.fixed_wpm = "fixed", 5
    audio = np.concatenate([transmission("5", 5, 15), np.zeros(8000 * 4, dtype=np.float32)])
    assert [m.text for m in eng.feed(audio, now=0)] == ["5"]
