"""Listener behaviour, driven by scripted audio instead of a microphone.

These cover the failure seen in live over-the-air testing: the wake word fires
on a partial result, the tail of "hey odin" lands in the command recogniser,
and the command window used to end on that garbage before the speaker had
said anything.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from odin.config import Config
from odin.listen import HEARD, NO_SPEECH, CommandListener

GRAMMAR = json.dumps(["volume up", "[unk]"])
LOUD = (np.ones(2000, dtype=np.int16) * 1200).tobytes()   # 0.125s of "speech"
QUIET = np.zeros(2000, dtype=np.int16).tobytes()          # 0.125s of silence


class ScriptedMic:
    """Returns the given blocks, then None forever."""

    def __init__(self, blocks):
        self.blocks = list(blocks)

    def drain(self):
        pass

    def discard_for(self, seconds):
        pass

    def read(self, timeout=0.5):
        return self.blocks.pop(0) if self.blocks else None


class StubRecogniser:
    """Ends an utterance on chosen block indices, with chosen results."""

    def __init__(self, endpoints=(), results=(), final="volume up"):
        self.endpoints = set(endpoints)
        self.results = list(results)
        self.final = final
        self.calls = 0
        self.resets = 0

    def Reset(self):
        self.resets += 1

    def AcceptWaveform(self, block):
        self.calls += 1
        return self.calls in self.endpoints

    def Result(self):
        text = self.results.pop(0) if self.results else ""
        return json.dumps({"text": text})

    def FinalResult(self):
        return json.dumps({"text": self.final})


def make_listener(rec):
    cfg = Config({})
    listener = CommandListener(model=None, cfg=cfg, sample_rate=16000)
    listener._recognisers[GRAMMAR] = rec
    return listener


class TestListener(unittest.TestCase):
    def test_command_is_returned_when_vosk_endpoints(self):
        rec = StubRecogniser(endpoints={2}, results=["volume up"])
        listener = make_listener(rec)
        result = listener.listen(ScriptedMic([LOUD, LOUD]), GRAMMAR)
        self.assertEqual(result.text, "volume up")
        self.assertEqual(result.reason, HEARD)

    def test_silence_ends_the_command(self):
        rec = StubRecogniser(final="volume up")
        listener = make_listener(rec)
        blocks = [LOUD, LOUD] + [QUIET] * 8  # 1.0s of silence > 0.8s
        result = listener.listen(ScriptedMic(blocks), GRAMMAR)
        self.assertEqual(result.text, "volume up")

    def test_garbage_endpoint_does_not_end_the_turn(self):
        # Block 1 ends an utterance with nothing usable (the wake-word tail);
        # the real command follows and must still be heard.
        rec = StubRecogniser(endpoints={1, 4}, results=["", "volume up"])
        listener = make_listener(rec)
        result = listener.listen(
            ScriptedMic([LOUD, QUIET, LOUD, LOUD]), GRAMMAR)
        self.assertEqual(result.text, "volume up")
        self.assertEqual(result.reason, HEARD)

    def test_unusable_final_restarts_instead_of_failing(self):
        # Silence ends an utterance that yields nothing: the recogniser is
        # reset and listening continues rather than reporting failure.
        rec = StubRecogniser(final="")
        listener = make_listener(rec)
        blocks = [LOUD] + [QUIET] * 8 + [LOUD]
        listener.listen(ScriptedMic(blocks), GRAMMAR)
        self.assertGreaterEqual(rec.resets, 1)

    def test_no_speech_when_only_silence(self):
        rec = StubRecogniser(final="")
        listener = make_listener(rec)
        result = listener.listen(ScriptedMic([QUIET] * 4), GRAMMAR)
        self.assertEqual(result.reason, NO_SPEECH)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
