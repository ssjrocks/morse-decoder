# Morse Decoder

Decodes the Morse (MCW) that [Talkpod Super Firmware](https://github.com/ssjrocks/talkpod-super-firmware)
sends from its Morse screen, by listening to whatever is playing on this computer: a WebSDR in the
browser, an SDR program, a radio on line-in played through the speakers. Nothing has to be routed:
it records the output device's monitor through PipeWire, so you still hear the audio as normal.

The signal is described in the firmware's [docs/morse.md](https://github.com/ssjrocks/talkpod-super-firmware/blob/main/docs/morse.md):
a hard-keyed 700 Hz tone on an FM carrier, 5–40 WPM (unit = floor(1200 / WPM) ms).

## Install

Needs Ubuntu with PipeWire (`pw-record`) and `python3-numpy python3-scipy python3-pyqt6`.

```bash
scripts/install.sh
```

Then open **Morse Decoder** from the app menu. `scripts/install.sh --uninstall` removes it.
From a checkout without installing: `PYTHONPATH=src python3 -m morse_decoder`.

## Use

1. Tune the WebSDR (or other receiver) to the channel in **FM** mode and play it.
2. The app starts listening to the default output. Pick another output under **Listen to** if the
   audio goes somewhere else.
3. Transmit from the radio's Morse screen. The text builds up as it arrives. When the transmission
   ends (a long silence), it goes into the list with the time, the dots and dashes, the speed it
   measured and how closely the timing fitted.

The window shows:

- **Spectrum**, 200–1600 Hz. The orange band is what the decoder listens to. The Talkpod's tone shows
  as a peak at 700 Hz. Click a peak to tune to it, or press **Find tone** to pick the strongest tone of
  the last 5 seconds. **700** goes back to the Talkpod's frequency.
- **Scope**, the last 4 seconds: the tone's share of the audio (0 to 1), the on (dashed) and off
  (dotted) thresholds, and green where the decoder hears the tone.
- **Speed**. *Auto (radio speeds)* fits the timing to the radio's eight settings. *Auto (any speed)*
  is for other senders. Pick a WPM if you know it. A message of only dits or only dahs with no
  gaps inside a letter can't be told apart by timing alone: a lone `E` or `5` at 5 WPM times exactly
  like `T` or `TTTTT` at 15 WPM. Auto settles these by the last speed it decoded.
- **Sensitivity**. Raise it for weak, noisy signals; lower it if noise makes false dits.

Decoded messages are also appended to `~/.local/state/morse-decoder/messages.txt` (tab-separated:
time, text, code, speed).

## How it decodes

1. Audio at 8 kHz mono from `pw-record` (the output's monitor).
2. The tone's power (±50 Hz around the tone, Bessel filters, which don't ring) divided by the power
   of the whole 300–3000 Hz band, every millisecond. A keyed tone on FM gives nearly 1. Receiver
   noise, voice and silence give a few percent, at any volume. CTCSS/DCS, the 55 Hz tail tone and the
   1000/850 Hz Roger beep fall outside the tone filter.
3. Hysteresis and a debounce turn that into tone on/off runs.
4. Each transmission is re-fitted after every run: the unit, and an edge bias from the receiver's
   filters, are chosen so that tones are 1 or 3 units and gaps 1, 3 or 7. Then on < 2 units is a dit,
   off 2–5 units ends a letter, off > 5 is a word space (as in docs/morse.md). A silence of 12 units
   (at least 1.2 s) ends the transmission.

## Tests

```bash
python3 -m pytest tests                              # decoder, synthetic transmissions
QT_QPA_PLATFORM=offscreen python3 tests/gui_smoke.py # the window, fed synthetic audio
python3 -m morse_decoder.synth "CQ TEST" --wpm 30 -o test.wav   # make a test signal (from src/)
```

The synthetic signal follows docs/morse.md: the 500 ms lead-in, hard keying, the Roger beep, the 55 Hz
tail, CTCSS, receiver noise, a 300–3000 Hz receiver filter and de-emphasis.

## Problem reports

**Report a problem…** sends your note, the app's log (`~/.local/state/morse-decoder/app.log`) and its
settings (tone, speed, sensitivity, whether the default output is used, the audio level) to the
developer's server. Crashes are sent the same way on the next start. The decoded messages are never
sent.
