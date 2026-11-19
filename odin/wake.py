"""Wake-word detection.

`WakeEngine` is deliberately a small interface. The shipped implementation
spots the wake phrase with Vosk restricted to a closed grammar, which needs no
training and works offline immediately. If false triggers ever become annoying,
a trained openWakeWord model can be dropped in behind this same interface
without the daemon changing.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from .audio import rms

log = logging.getLogger("odin.wake")

# Capture block length assumed when sizing the peak window, in seconds. The
# daemon's blocks are configurable; this only decides how many of them make up
# "roughly the length of a wake word", so an approximation is fine.
_ASSUMED_BLOCK_S = 0.125


@dataclass(frozen=True)
class WakeEvent:
    phrase: str
    text: str
    partial: bool


class WakeEngine(Protocol):
    def process(self, block: bytes) -> WakeEvent | None: ...
    def reset(self) -> None: ...


class VoskWakeEngine:
    """Spots the wake phrase using a grammar-restricted Vosk recogniser."""

    def __init__(
        self,
        model,
        grammar: str,
        phrases: list[str] | None = None,
        sample_rate: int = 16000,
        on_partial: bool = True,
        refractory_s: float = 1.5,
        min_level: float = 0.0,
        speak_up_margin: float = 2.0,
        peak_window_s: float = 0.9,
    ):
        """`grammar` is what the recogniser may hear; `phrases` is the subset
        that wakes ODIN. They differ: the grammar also holds decoy words whose
        only job is to give the decoder an alternative to the wake word."""
        from vosk import KaldiRecognizer

        self._rec = KaldiRecognizer(model, sample_rate, grammar)
        if phrases is None:  # fall back to treating the grammar as the wake set
            phrases = [p for p in json.loads(grammar) if not p.startswith("[")]
        self._phrases = [p.lower().strip() for p in phrases if p]
        self._on_partial = on_partial
        self._refractory_s = refractory_s
        self._last_fire = 0.0
        self._sample_rate = sample_rate

        # --- telling a person apart from the laptop's own speakers ---------
        #
        # A closed grammar has to map whatever it hears onto something in it,
        # so a film or a call playing nearby eventually decodes as "hey odin"
        # - which is what was happening. The discriminator is not a pause: a
        # film does not pause, and ODIN has to stay usable *while* one plays
        # (that is exactly when "subtitle" is wanted). It is level. Whatever
        # is playing settles into the background, and someone addressing the
        # laptop is louder than it.
        self._min_level = max(0.0, float(min_level))
        self._speak_up_margin = max(1.0, float(speak_up_margin))
        # Background follows the noise floor: quick to fall, slow to rise, so
        # a few spoken words cannot drag it up, while a film that has been
        # playing for a while does become the background.
        self._background: float | None = None
        # Peak level over roughly the length of a wake word, which is what a
        # detection has to clear.
        self._recent: list[float] = []
        self._peak_blocks = max(1, int(float(peak_window_s) / _ASSUMED_BLOCK_S))
        log.info("wake phrases: %s", ", ".join(self._phrases))

    # -- gating -----------------------------------------------------------
    @property
    def _gate_off(self) -> bool:
        return self._min_level <= 0.0

    def _track_level(self, block: bytes) -> None:
        """Follow the background level and the recent peak."""
        if self._gate_off:
            return
        level = rms(block)
        self._recent.append(level)
        if len(self._recent) > self._peak_blocks:
            self._recent.pop(0)
        if self._background is None:
            self._background = level
        elif level < self._background:
            self._background += 0.30 * (level - self._background)  # fast down
        else:
            self._background += 0.02 * (level - self._background)  # slow up

    def _loud_enough(self) -> bool:
        """True if the last second cleared both the floor and the background.

        Two bars, because either on its own is wrong: the floor alone lets a
        film through as soon as it is louder than the floor, and the margin
        alone lets a whisper through in a silent room.
        """
        if self._gate_off or not self._recent:
            return True
        peak = max(self._recent)
        background = self._background if self._background is not None else 0.0
        return (peak >= self._min_level
                and peak >= background * self._speak_up_margin)

    # -- interface --------------------------------------------------------
    def reset(self) -> None:
        self._rec.Reset()
        # Only the peak window is cleared. The background is long-running
        # knowledge about the room and must survive a reset, or the next
        # detection has nothing to be measured against.
        self._recent.clear()

    def process(self, block: bytes) -> WakeEvent | None:
        self._track_level(block)
        if self._rec.AcceptWaveform(block):
            text = json.loads(self._rec.Result()).get("text", "")
            return self._evaluate(text, partial=False)
        if self._on_partial:
            text = json.loads(self._rec.PartialResult()).get("partial", "")
            return self._evaluate(text, partial=True)
        return None

    # -- internals --------------------------------------------------------
    def _evaluate(self, text: str, partial: bool) -> WakeEvent | None:
        normalised = " ".join((text or "").split()).lower()
        if not normalised:
            return None
        phrase = self._hit(normalised)
        if phrase is None:
            return None
        if not self._loud_enough():
            # No louder than whatever is already playing, so this is the
            # playback talking and not a person.
            log.debug("ignoring %r: no louder than the background (%.0f)",
                      normalised, self._background or 0.0)
            self.reset()
            return None
        now = time.monotonic()
        if now - self._last_fire < self._refractory_s:
            # Same utterance still echoing through the partial results.
            self.reset()
            return None
        self._last_fire = now
        self.reset()
        return WakeEvent(phrase=phrase, text=normalised, partial=partial)

    def _hit(self, text: str) -> str | None:
        """Longest wake phrase that the text ends with (or equals)."""
        best: str | None = None
        for phrase in self._phrases:
            if text == phrase or text.endswith(" " + phrase) or text.endswith(phrase):
                if best is None or len(phrase) > len(best):
                    best = phrase
        return best
