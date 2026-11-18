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
        self.last_said = None

    def say(self, text):
        self.said.append(text)
        self.last_said = text

    def beep(self, name="ok"):
        self.beeps.append(name)


class StubListener:
    """Returns a scripted sequence of results, one per listen() call."""

    def __init__(self, *results):
        self.results = list(results)
        self.grammars: list[str] = []
        self.timeouts: list[float | None] = []
        self.scales: list[float] = []

    def listen(self, mic, grammar, no_speech_timeout=None, threshold_scale=1.0):
        self.grammars.append(grammar)
        self.timeouts.append(no_speech_timeout)
        self.scales.append(threshold_scale)
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
        self.cfg = Config({
            "apps": {"firefox": "firefox_firefox"},
            "listen": {"follow_up_s": 0.0},  # one turn unless a test says otherwise
        })
        self.odin = object.__new__(Odin)
        self.odin.cfg = self.cfg
        self.odin.table = IntentTable(self.cfg)
        self.odin.wake_phrases = {"hey odin", "hi odin"}
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

    def set_cfg(self, **sections):
        base = {"apps": {"firefox": "firefox_firefox"},
                "listen": {"follow_up_s": 0.0}}
        for name, values in sections.items():
            merged = dict(base.get(name, {}))
            merged.update(values)
            base[name] = merged
        self.odin.cfg = Config(base)


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

    def test_intent_without_a_reply_only_beeps(self):
        self.returns = None  # meta.cancel has nothing to say
        self.script(heard("never mind"))
        self.odin._handle_wake()
        self.assertIn("ok", self.odin.speaker.beeps)
        self.assertEqual(self.odin.speaker.said, [])

    def test_courtesy_reply_is_spoken_when_chatty(self):
        self.returns = None  # falls back to the intent's own reply
        self.script(heard("pause"))
        self.odin._handle_wake()
        self.assertIn("Paused.", self.odin.speaker.said)

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


class TestVerbosity(DaemonTestCase):
    def test_chatty_speaks_courtesy_replies(self):
        self.set_cfg(speech={"verbosity": "chatty"})
        self.returns = "Volume 70 percent."
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertIn("Volume 70 percent.", self.odin.speaker.said)

    def test_brief_beeps_instead_of_courtesy_replies(self):
        self.set_cfg(speech={"verbosity": "brief"})
        self.returns = "Volume 70 percent."
        self.script(heard("volume up"))
        self.odin._handle_wake()
        self.assertEqual(self.odin.speaker.said, [])
        self.assertIn("ok", self.odin.speaker.beeps)

    def test_answers_are_spoken_even_when_brief(self):
        # A question's reply *is* the answer, so verbosity must not mute it.
        self.set_cfg(speech={"verbosity": "brief"})
        self.returns = "It is nine P M."
        self.script(heard("what time is it"))
        self.odin._handle_wake()
        self.assertIn("It is nine P M.", self.odin.speaker.said)

    def test_errors_are_spoken_even_when_brief(self):
        self.set_cfg(speech={"verbosity": "brief"})
        self.raises = ActionError("No media player is running.")
        self.script(heard("pause"))
        self.odin._handle_wake()
        self.assertIn("No media player is running.", self.odin.speaker.said)


class TestFollowUp(DaemonTestCase):
    def test_a_second_command_needs_no_wake_word(self):
        self.set_cfg(listen={"follow_up_s": 4.0})
        self.script(heard("volume up"), heard("pause"),
                    ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual([c[0] for c in self.calls], ["volume.up", "media.pause"])

    def test_only_one_wake_beep_for_the_whole_exchange(self):
        self.set_cfg(listen={"follow_up_s": 4.0})
        self.script(heard("volume up"), heard("pause"),
                    ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual(self.odin.speaker.beeps.count("wake"), 1)

    def test_silence_in_the_follow_up_window_is_silent(self):
        # The window closing is normal, so it must not beep a failure.
        self.set_cfg(listen={"follow_up_s": 4.0})
        self.script(heard("volume up"), ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertNotIn("fail", self.odin.speaker.beeps)

    def test_follow_up_waits_a_shorter_time(self):
        self.set_cfg(listen={"follow_up_s": 4.0})
        self.script(heard("volume up"), ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual(self.odin.listener.timeouts, [None, 4.0])

    def test_turn_limit_is_enforced(self):
        self.set_cfg(listen={"follow_up_s": 4.0, "max_turns": 2})
        self.script(*[heard("volume up")] * 6)
        self.odin._handle_wake()
        self.assertEqual(len(self.calls), 2)

    def test_zero_disables_the_window(self):
        self.set_cfg(listen={"follow_up_s": 0.0})
        self.script(heard("volume up"), heard("pause"))
        self.odin._handle_wake()
        self.assertEqual(len(self.calls), 1)


class TestReWake(DaemonTestCase):
    """The wake word must never be mistaken for a command.

    Observed live: with a follow-up window open, a second "hey odin" reached
    the command recogniser, came back as "louder", and changed the volume.
    """

    def test_wake_word_does_not_run_a_command(self):
        self.set_cfg(listen={"follow_up_s": 4.0})
        self.script(heard("hey odin"), ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])

    def test_wake_word_beeps_and_keeps_listening(self):
        self.set_cfg(listen={"follow_up_s": 4.0})
        self.script(heard("hey odin"), heard("volume up"),
                    ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual([c[0] for c in self.calls], ["volume.up"])
        self.assertEqual(self.odin.speaker.beeps.count("wake"), 2)

    def test_wake_word_anywhere_in_the_hypotheses_blocks_the_command(self):
        # The exact live failure: ["louder", "hey odin", "odin"] ran volume.up.
        self.set_cfg(listen={"follow_up_s": 4.0})
        self.script(
            ListenResult("louder", HEARD, ("louder", "hey odin", "odin")),
            ListenResult("", NO_SPEECH),
        )
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])

    def test_follow_up_demands_a_louder_voice(self):
        # Room noise should not reach the recogniser in an unprompted window.
        self.set_cfg(listen={"follow_up_s": 4.0,
                             "follow_up_threshold_scale": 2.0})
        self.script(heard("volume up"), ListenResult("", NO_SPEECH))
        self.odin._handle_wake()
        self.assertEqual(self.odin.listener.scales, [1.0, 2.0])


class TestClarification(DaemonTestCase):
    def test_near_miss_offers_two_options(self):
        self.script(heard("brightness"), heard("brightness up"))
        self.odin._handle_wake()
        question = self.odin.speaker.said[0]
        self.assertTrue(question.startswith("Did you mean"), question)
        self.assertEqual([c[0] for c in self.calls], ["system.brightness_up"])

    def test_truncated_command_is_completed(self):
        # "lock" is below the similarity cutoff but is a clear word-prefix.
        self.script(heard("lock"), heard("yes"))
        self.odin._handle_wake()
        self.assertEqual([c[0] for c in self.calls], ["system.lock"])

    def test_declining_does_nothing(self):
        self.script(heard("brightness"), heard("no"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])
        self.assertIn("Never mind.", self.odin.speaker.said)

    def test_yes_is_ambiguous_with_two_options(self):
        self.script(heard("brightness"), heard("yes"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])

    def test_will_not_guess_when_too_many_commands_fit(self):
        # A bare verb fits dozens of phrases; guessing two at random is worse
        # than saying so.
        self.set_cfg(apps={
            "firefox": "firefox_firefox", "chrome": "google-chrome",
            "spotify": "spotify_spotify", "telegram": "telegram",
            "settings": "org.gnome.Settings", "files": "org.gnome.Nautilus",
        })
        self.odin.table = IntentTable(self.odin.cfg)
        self.script(heard("open"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])
        self.assertIn("I did not get that.", self.odin.speaker.said)
        self.assertFalse(any(t.startswith("Did you mean")
                             for t in self.odin.speaker.said))

    def test_yes_accepts_a_single_suggestion(self):
        # Pinned to one option: with a single offer, "yes" is unambiguous.
        only = self.odin.table.match("volume up")
        self.odin._suggest = lambda result: [("volume up", only)]
        self.script(heard("volume"), heard("yes"))
        self.odin._handle_wake()
        self.assertEqual([c[0] for c in self.calls], ["volume.up"])

    def test_two_options_are_both_offered_by_name(self):
        self.script(heard("brightness"), heard("brightness down"))
        self.odin._handle_wake()
        question = self.odin.speaker.said[0]
        self.assertIn(" or ", question)
        self.assertEqual([c[0] for c in self.calls], ["system.brightness_down"])

    def test_destructive_intents_are_never_suggested(self):
        result = ListenResult("shut", "heard", ("shut",))
        for _, match in self.odin._suggest(result):
            self.assertFalse(match.intent.confirm, match.intent.name)

    def test_nonsense_is_not_clarified(self):
        self.script(heard("the"))
        self.odin._handle_wake()
        self.assertIn("I did not get that.", self.odin.speaker.said)

    def test_clarify_can_be_disabled(self):
        self.set_cfg(listen={"clarify": False})
        self.script(heard("brightness"))
        self.odin._handle_wake()
        self.assertEqual(self.calls, [])
        self.assertIn("I did not get that.", self.odin.speaker.said)
