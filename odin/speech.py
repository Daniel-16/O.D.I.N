"""ODIN's voice: Piper speech synthesis plus short confirmation tones.

Both run fully offline. The microphone is muted for the duration of any
playback, so ODIN never transcribes its own voice or its own beeps.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Iterator

import numpy as np

log = logging.getLogger("odin.speech")

WAKE = "wake"
OK = "ok"
FAIL = "fail"
ASK = "ask"

# name -> (frequency Hz, duration s)
_TONES: dict[str, tuple[float, float]] = {
    WAKE: (880.0, 0.09),
    OK: (1320.0, 0.07),
    FAIL: (300.0, 0.18),
    ASK: (660.0, 0.08),
}

_TONE_RATE = 22050


def _tone(freq: float, seconds: float, rate: int = _TONE_RATE) -> np.ndarray:
    """A sine burst with short fades, so it clicks neither in nor out."""
    n = int(rate * seconds)
    t = np.arange(n, dtype=np.float32) / rate
    wave = np.sin(2 * np.pi * freq * t, dtype=np.float32)
    fade = max(1, int(rate * 0.008))
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    wave[:fade] *= ramp
    wave[-fade:] *= ramp[::-1]
    return wave


class Speaker:
    def __init__(self, cfg, mic=None):
        self._cfg = cfg["speech"]
        self._voice_path = cfg.voice_path
        self._mic = mic
        self._voice = None
        self._lock = threading.Lock()
        self._gain = float(self._cfg.get("volume", 0.5))
        self.last_said: str | None = None  # for "say that again"
        # When that reply finished playing. The daemon uses it to recognise
        # its own words coming back through the microphone.
        self.last_said_at: float = 0.0
        self._cache: dict[str, np.ndarray] = {}
        if self._cfg.get("enabled", True) and self._cfg.get("preload", True):
            self.warm()

    # -- Piper ------------------------------------------------------------
    def warm(self) -> None:
        """Load the voice model up front so the first reply is not delayed."""
        if self._voice is not None:
            return
        if not self._voice_path.exists():
            log.warning("voice model missing: %s (speech disabled)", self._voice_path)
            return
        try:
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(self._voice_path))
            log.info("voice loaded: %s", self._voice_path.name)
        except Exception as exc:
            log.warning("could not load voice (%s); falling back to beeps", exc)
            self._voice = None

    # -- playback ---------------------------------------------------------
    @contextlib.contextmanager
    def _playback(self) -> Iterator[None]:
        """Mute the microphone around any sound ODIN makes."""
        if self._mic is not None:
            self._mic.mute()
        try:
            yield
        finally:
            if self._mic is not None:
                self._mic.unmute()

    def _play(self, samples: np.ndarray, rate: int) -> None:
        if samples.size == 0:
            return
        import sounddevice as sd  # see the note in audio.py
        audio = np.clip(samples * self._gain, -1.0, 1.0)
        with self._lock, self._playback():
            try:
                sd.play(audio, samplerate=rate, blocking=True)
            except Exception as exc:
                log.warning("playback failed: %s", exc)

    def beep(self, name: str = OK) -> None:
        if not self._cfg.get("beeps", True):
            return
        if name not in self._cache:
            freq, seconds = _TONES.get(name, _TONES[OK])
            self._cache[name] = _tone(freq, seconds)
        self._play(self._cache[name], _TONE_RATE)

    def say(self, text: str) -> None:
        if not text or not self._cfg.get("enabled", True):
            return
        self.last_said = text
        self.warm()
        if self._voice is None:
            self.beep(OK)
            return
        try:
            chunks = list(self._voice.synthesize(text))
        except Exception as exc:
            log.warning("synthesis failed: %s", exc)
            return
        if not chunks:
            return
        rate = chunks[0].sample_rate
        audio = np.concatenate(
            [c.audio_int16_array.astype(np.float32) / 32768.0 for c in chunks]
        )
        log.debug("speaking: %s", text)
        self._play(audio, rate)
        self.last_said_at = time.monotonic()
