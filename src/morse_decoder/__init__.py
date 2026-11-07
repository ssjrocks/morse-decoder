"""Morse Decoder: decodes Talkpod Super Firmware Morse (MCW) from any audio playing on this PC."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("morse-decoder")
except PackageNotFoundError:  # running from a checkout without installing
    __version__ = "0.0.0+checkout"
APP_ID = "io.github.ssjrocks.MorseDecoder"
APP_NAME = "Morse Decoder"
