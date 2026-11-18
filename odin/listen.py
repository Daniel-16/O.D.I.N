"""Capturing one spoken command after the wake word.

Endpointing is energy based rather than purely Vosk's own: after the beep we
wait for speech, then end the command once the microphone has been quiet for
`silence_end_s`. That gives a predictable, short turnaround and avoids hanging
on until Vosk decides an utterance is over.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from .audio import Microphone, rms

log = logging.getLogger("odin.listen")

HEARD = "heard"
NO_SPEECH = "no_speech"
TIMEOUT = "timeout"


@dataclass(frozen=True)
class ListenResult:
    text: str
    reason: str
    candidates: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.text)


def make_recogniser(model, grammar: str, sample_rate: int, alternatives: int):
    """A grammar-restricted recogniser, optionally returning N-best results.

    N-best matters for short commands. A single word carries little acoustic
    evidence, so the top hypothesis is sometimes a common function word from
    the grammar's bigram model; the correct command is then second or third.
    The caller filters the alternatives through the intent table and keeps the
    best one that is actually a command, which is strictly more robust than
    trusting the top hypothesis alone.
    """
    from vosk import KaldiRecognizer

    rec = KaldiRecognizer(model, sample_rate, grammar)
    if alternatives and alternatives > 1:
        rec.SetMaxAlternatives(int(alternatives))
    return rec


def candidates_from(payload: str) -> list[str]:
    """Ordered, cleaned hypotheses from a Vosk result document.

    Handles both result shapes: {"text": ...} in plain mode and
    {"alternatives": [{"text": ..., "confidence": ...}]} in N-best mode.
    Vosk returns alternatives already ordered by confidence.
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return []
    raw: list[str]
    if isinstance(data.get("alternatives"), list):
        raw = [alt.get("text", "") for alt in data["alternatives"]]
    else:
        raw = [data.get("text", "")]
    out: list[str] = []
    for text in raw:
        cleaned = _clean(text)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


class CommandListener:
    def __init__(self, model, cfg, sample_rate: int = 16000):
        self._model = model
        self._sample_rate = sample_rate
        self._cfg = cfg["listen"]
        self._recognisers: dict[str, object] = {}

    def _recogniser(self, grammar: str):
        """Recognisers are cached per grammar; building one compiles an FST."""
        rec = self._recognisers.get(grammar)
        if rec is None:
            rec = make_recogniser(
                self._model, grammar, self._sample_rate,
                int(self._cfg.get("alternatives", 5)),
            )
            self._recognisers[grammar] = rec
        else:
            rec.Reset()
        return rec

    def listen(self, mic: Microphone, grammar: str) -> ListenResult:
        rec = self._recogniser(grammar)
        threshold = float(self._cfg["energy_threshold"])
        silence_end = float(self._cfg["silence_end_s"])
        no_speech_timeout = float(self._cfg["no_speech_timeout_s"])
        max_s = float(self._cfg["max_s"])

        started = time.monotonic()
        speech_seen = False
        silence_for = 0.0
        # When we are waiting for speech to begin. Reset whenever we discard a
        # false start, so the speaker always gets the full window to talk.
        quiet_since: float | None = started

        mic.drain()
        while True:
            now = time.monotonic()
            if now - started > max_s:
                return self._final(rec, TIMEOUT)

            block = mic.read(timeout=0.5)
            if block is None:
                if (not speech_seen and quiet_since is not None
                        and now - quiet_since > no_speech_timeout):
                    return ListenResult("", NO_SPEECH)
                continue

            block_s = len(block) / 2 / self._sample_rate
            loud = rms(block) >= threshold

            if rec.AcceptWaveform(block):
                found = candidates_from(rec.Result())
                if found:
                    return ListenResult(found[0], HEARD, tuple(found))
                # Vosk ended an utterance that held no usable command - most
                # often the tail of the wake word itself. Keep listening
                # rather than reporting failure before the speaker has begun.
                speech_seen, silence_for = False, 0.0
                quiet_since = time.monotonic()
                continue

            if loud:
                speech_seen = True
                silence_for = 0.0
                quiet_since = None
            else:
                silence_for += block_s

            if speech_seen and silence_for >= silence_end:
                result = self._final(rec, HEARD)
                if result.ok:
                    return result
                # Same again: silence ended something unusable. Start over
                # with whatever time is left.
                rec.Reset()
                speech_seen, silence_for = False, 0.0
                quiet_since = time.monotonic()
                continue

            if (not speech_seen and quiet_since is not None
                    and time.monotonic() - quiet_since > no_speech_timeout):
                return ListenResult("", NO_SPEECH)

    @staticmethod
    def _final(rec, reason: str) -> ListenResult:
        found = candidates_from(rec.FinalResult())
        if not found:
            return ListenResult("", NO_SPEECH)
        return ListenResult(found[0], reason, tuple(found))


def _clean(text: str) -> str:
    """Normalise whitespace and drop Vosk's unknown-token marker."""
    words = [w for w in (text or "").split() if w != "[unk]"]
    return " ".join(words).lower()
