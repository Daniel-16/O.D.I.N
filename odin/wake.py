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

log = logging.getLogger("odin.wake")


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
        sample_rate: int = 16000,
        on_partial: bool = True,
        refractory_s: float = 1.5,
    ):
        from vosk import KaldiRecognizer

        self._rec = KaldiRecognizer(model, sample_rate, grammar)
        self._phrases = [p for p in json.loads(grammar) if not p.startswith("[")]
        self._on_partial = on_partial
        self._refractory_s = refractory_s
        self._last_fire = 0.0
        log.info("wake phrases: %s", ", ".join(self._phrases))

    def reset(self) -> None:
        self._rec.Reset()

    def process(self, block: bytes) -> WakeEvent | None:
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
