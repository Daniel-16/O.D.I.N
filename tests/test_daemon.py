"""State-machine tests for the daemon, with no microphone and no model.

The Odin object is built with object.__new__ and given stub collaborators, so
the wake -> listen -> confirm -> act logic can be checked directly. This covers
the parts that are awkward to exercise by speaking: the confirmation prompt,
standby, and what happens when an action fails.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odin import daemon as daemon_mod
from odin.actions import ActionError, Context
from odin.config import Config
from odin.daemon import Odin
from odin.intents import IntentTable
from odin.listen import HEARD, NO_SPEECH, ListenResult


class StubSpeaker:
    def __init__(self):
        self.said: list[str] = []
        self.beeps: list[str] = []

    def say(self, text):
        self.said.append(text)

    def beep(self, name="ok"):
        self.beeps.append(name)


class StubListener:
    """Returns a scripted sequence of results, one per listen() call."""

    def __init__(self, *results):
        self.results = list(results)
        self.grammars: list[str] = []

    def listen(self, mic, grammar):
        self.grammars.append(grammar)
        return self.results.pop(0) if self.results else ListenResult("", NO_SPEECH)


class StubMic:
    def __init__(self):
        self.discarded: list[float] = []

    def drain(self):
        pass

    def discard_for(self, seconds):
        self.discarded.append(seconds)


def heard(text):
    return ListenResult(text, HEARD, (text,))


class DaemonTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = Config({"apps": {"firefox": "firefox_firefox"}})
        self.odin = object.__new__(Odin)
        self.odin.cfg = self.cfg
        self.odin.table = IntentTable(self.cfg)
        self.odin.command_grammar = self.odin.table.command_grammar()
        self.odin.speaker = StubSpeaker()
        self.odin.mic = StubMic()
        self.odin.ctx = Context(cfg=self.cfg, speaker=self.odin.speaker)

        self.calls: list[tuple[str, dict]] = []
        self.raises: Exception | None = None
        self.returns: str | None = None

        def fake_dispatch(handler, ctx, slots):
            self.calls.append((handler, slots))
            if self.raises is not None:
                raise self.raises
            return self.returns

        self._real_dispatch = daemon_mod.dispatch
        daemon_mod.dispatch = fake_dispatch

    def tearDown(self):
        daemon_mod.dispatch = self._real_dispatch

    def script(self, *results):
        self.odin.listener = StubListener(*results)


class TestNormalCommands(DaemonTestCase):
    def test_command_is_dispatched(self):
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [("volume.up", {})])

    def test_wake_beeps_before_listening(self):
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertEqual(self.odin.speaker.beeps[0], "wake")

    def test_wake_word_tail_is_discarded_before_listening(self):
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertEqual(self.odin.mic.discarded, [0.3])

    def test_slots_are_passed_through(self):
        self.script(heard("set volume to thirty percent"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [("volume.set_level", {"percent": 30})])

    def test_returned_text_is_spoken(self):
        self.returns = "Volume 30 percent."
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertIn("Volume 30 percent.", self.odin.speaker.said)

    def test_silent_success_only_beeps(self):
        self.returns = None  # media.pause has no spoken reply
        self.script(heard("pause"))
        self.odin._handle_wake()
        self.assertIn("ok", self.odin.speaker.beeps)
        self.assertEqual(self.odin.speaker.said, [])

    def test_nbest_candidate_is_used(self):
        # Top hypothesis is noise; the real command is second.
        self.script(ListenResult("cursor", HEARD, ("cursor", "pause")))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [("media.pause", {})])


class TestFailureHandling(DaemonTestCase):
    def test_no_speech_does_nothing(self):
        self.script(ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])
        self.assertIn("fail", self.odin.speaker.beeps)

    def test_unknown_command_is_reported(self):
        self.script(heard("make me a sandwich"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])
        self.assertIn("I did not get that.", self.odin.speaker.said)

    def test_action_error_is_spoken(self):
        self.raises = ActionError("No media player is running.")
        self.script(heard("pause"))
        self.odin._handle_wake()
        self.assertIn("No media player is running.", self.odin.speaker.said)
        self.assertIn("fail", self.odin.speaker.beeps)

    def test_unexpected_exception_does_not_escape(self):
        self.raises = RuntimeError("boom")
        self.script(heard("pause"))
        self.odin._handle_wake()  # must not raise
        self.assertIn("Something went wrong.", self.odin.speaker.said)


class TestConfirmation(DaemonTestCase):
    def test_yes_proceeds(self):
        self.script(heard("shut down"), heard("yes"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [("system.shutdown", {})])

    def test_no_cancels(self):
        self.script(heard("shut down"), heard("no"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])
        self.assertIn("Cancelled.", self.odin.speaker.said)

    def test_silence_cancels(self):
        self.script(heard("shut down"), ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])

    def test_ambiguous_reply_cancels(self):
        # "yes" and "no" both among the hypotheses must not count as consent.
        self.script(heard("reboot"), ListenResult("yes", HEARD, ("yes", "no")))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])

    def test_confirmation_can_be_disabled(self):
        self.odin.cfg = Config({"system": {"confirm_destructive": False}})
        self.script(heard("shut down"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [("system.shutdown", {})])

    def test_non_destructive_is_not_confirmed(self):
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [("volume.up", {})])
        self.assertNotIn("Are you sure?", self.odin.speaker.said)


class TestStandby(DaemonTestCase):
    def test_commands_are_ignored_in_standby(self):
        self.odin.ctx.standby = True
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])

    def test_resume_is_honoured_in_standby(self):
        self.odin.ctx.standby = True
        self.script(heard("start listening"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [("meta.resume", {})])

    def test_standby_uses_the_restricted_grammar(self):
        self.odin.ctx.standby = True
        self.script(heard("start listening"))
        self.odin._handle_wake()
        self.assertEqual(self.odin.listener.grammars[0], daemon_mod._RESUME_GRAMMAR)

    def test_normal_mode_uses_the_full_grammar(self):
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertEqual(self.odin.listener.grammars[0], self.odin.command_grammar)


if __name__ == "__main__":
    unittest.main(verbosity=2)
