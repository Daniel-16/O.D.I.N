"""Unit tests that need neither a microphone nor the speech model."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from odin.audio import rms
from odin.config import Config
from odin.intents import (DEFAULT_WAKE_DECOYS, IntentTable, confirm_grammar,
                          wake_grammar, wake_phrases)
from odin.listen import _clean, candidates_from
from odin.vocab import words_in
from odin.wake import VoskWakeEngine


def make_cfg(**overrides) -> Config:
    data = {"apps": {"firefox": "firefox_firefox", "media player": "vlc"}}
    data.update(overrides)
    return Config(data)


class TestConfig(unittest.TestCase):
    def test_defaults_fill_missing_sections(self):
        cfg = Config({})
        self.assertEqual(cfg["audio"]["sample_rate"], 16000)
        self.assertEqual(cfg["wake"]["phrases"], ["hey odin", "hi odin"])

    def test_user_values_override_defaults(self):
        cfg = Config({"volume": {"step": 17}})
        self.assertEqual(cfg["volume"]["step"], 17)
        # Untouched keys in the same section survive the merge.
        self.assertEqual(cfg["volume"]["limit"], 1.0)


class TestIntents(unittest.TestCase):
    def setUp(self):
        self.table = IntentTable(make_cfg())

    def test_every_phrase_maps_to_an_intent(self):
        for phrase in self.table.phrases:
            self.assertIsNotNone(self.table.match(phrase), phrase)

    def test_grammar_contains_unk_and_all_phrases(self):
        grammar = json.loads(self.table.command_grammar())
        self.assertIn("[unk]", grammar)
        for phrase in self.table.phrases:
            self.assertIn(phrase, grammar)

    def test_match_is_case_and_whitespace_insensitive(self):
        match = self.table.match("  VOLUME   UP ")
        self.assertIsNotNone(match)
        self.assertEqual(match.intent.name, "volume.up")

    def test_unknown_text_does_not_match(self):
        self.assertIsNone(self.table.match("make me a sandwich"))
        self.assertIsNone(self.table.match(""))
        self.assertIsNone(self.table.match("[unk]"))

    def test_volume_set_carries_a_percent_slot(self):
        match = self.table.match("set volume to seventy percent")
        self.assertEqual(match.slots["percent"], 70)
        self.assertEqual(match.intent.handler, "volume.set_level")

    def test_half_volume_is_fifty(self):
        self.assertEqual(self.table.match("half volume").slots["percent"], 50)

    def test_app_intents_carry_the_desktop_id(self):
        match = self.table.match("open media player")
        self.assertEqual(match.slots["desktop_id"], "vlc")
        self.assertEqual(match.intent.handler, "apps.launch")
        # All three verbs reach the same intent.
        for verb in ("open", "launch", "start"):
            self.assertIsNotNone(self.table.match(f"{verb} firefox"))

    def test_destructive_intents_require_confirmation(self):
        for phrase in ("shut down", "reboot"):
            self.assertTrue(self.table.match(phrase).intent.confirm, phrase)
        self.assertFalse(self.table.match("volume up").intent.confirm)

    def test_no_phrase_is_claimed_by_two_intents(self):
        seen: dict[str, str] = {}
        for intent in self.table.intents:
            for phrase in intent.phrases:
                key = phrase.lower()
                if key in seen:
                    self.assertEqual(
                        seen[key], intent.name,
                        f"phrase {key!r} is claimed by two intents",
                    )
                seen[key] = intent.name


class TestHandlers(unittest.TestCase):
    """Guards against a bug that actually happened.

    A handler named `set` or `max` shadows the builtin *inside its own
    module*, so an unrelated call to max() in the same file silently invokes
    the action instead. Handler names must therefore never collide with a
    builtin.
    """

    def setUp(self):
        self.table = IntentTable(make_cfg())

    def test_all_handlers_resolve(self):
        from odin.actions import resolve

        for intent in self.table.intents:
            self.assertTrue(callable(resolve(intent.handler)), intent.handler)

    def test_no_handler_shadows_a_builtin(self):
        import builtins

        for intent in self.table.intents:
            name = intent.handler.rpartition(".")[2]
            self.assertFalse(
                hasattr(builtins, name),
                f"{intent.handler} shadows the builtin {name!r}",
            )


class TestGrammars(unittest.TestCase):
    def test_bare_name_is_excluded_by_default(self):
        self.assertNotIn("odin", wake_phrases(make_cfg()))

    def test_bare_name_can_be_enabled(self):
        cfg = make_cfg(wake={"allow_bare_name": True})
        self.assertIn("odin", wake_phrases(cfg))

    def test_confirm_grammar_offers_yes_and_no(self):
        grammar = json.loads(confirm_grammar())
        self.assertIn("yes", grammar)
        self.assertIn("no", grammar)


class TestWakeGrammarDecoys(unittest.TestCase):
    """The wake grammar is wider than the wake phrases, on purpose.

    Reported live: a bare "hey" woke ODIN. A Vosk grammar is a bigram model
    over only its own words, so with nothing but {hey, hi, odin} in it "hey"
    had no legal continuation except "odin". Decoys give the decoder an
    alternative - but must never wake ODIN themselves.
    """

    def setUp(self):
        self.cfg = make_cfg()

    def test_grammar_contains_the_decoys(self):
        grammar = json.loads(wake_grammar(self.cfg))
        for word in ("hey", "hi", "hello", "okay", "there", "you"):
            self.assertIn(word, grammar, word)

    def test_wake_phrases_exclude_the_decoys(self):
        phrases = wake_phrases(self.cfg)
        self.assertEqual(phrases, ["hey odin", "hi odin"])
        for word in DEFAULT_WAKE_DECOYS:
            self.assertNotIn(word, phrases, word)

    def test_grammar_is_much_wider_than_the_wake_set(self):
        grammar = json.loads(wake_grammar(self.cfg))
        self.assertGreater(len(grammar), len(wake_phrases(self.cfg)) + 50)

    def test_extra_decoys_are_added(self):
        cfg = make_cfg(wake={"extra_decoys": ["bruv", "mate"]})
        grammar = json.loads(wake_grammar(cfg))
        self.assertIn("mate", grammar)
        self.assertNotIn("mate", wake_phrases(cfg))

    def test_the_unknown_token_is_present(self):
        self.assertIn("[unk]", json.loads(wake_grammar(self.cfg)))


class TestWakeMatching(unittest.TestCase):
    def setUp(self):
        # Built without a model: only the phrase matching is under test.
        self.engine = object.__new__(VoskWakeEngine)
        self.engine._phrases = ["hey odin", "hi odin"]

    def test_exact_phrase_matches(self):
        self.assertEqual(self.engine._hit("hey odin"), "hey odin")

    def test_trailing_phrase_matches(self):
        # Vosk partials often carry earlier words along.
        self.assertEqual(self.engine._hit("the weather hi odin"), "hi odin")

    def test_unrelated_text_does_not_match(self):
        self.assertIsNone(self.engine._hit("open the door"))
        self.assertIsNone(self.engine._hit(""))

    def test_phrase_must_end_the_text(self):
        self.assertIsNone(self.engine._hit("hey odin play some music"))

    def test_decoy_words_do_not_wake(self):
        for text in ("hey", "hi", "hello", "okay", "hey there", "hi there",
                     "odin", "hey you"):
            self.assertIsNone(self.engine._hit(text), text)


class TestWakeLevelGate(unittest.TestCase):
    """A wake word must be louder than whatever is already playing.

    With a closed grammar, continuous sound from the speakers - a film, a
    call, music - sooner or later decodes as the wake phrase; that is how
    ODIN was waking itself while a video played. The gate is level rather
    than silence, because ODIN has to stay usable *during* a film.
    """

    RATE = 16000

    def engine(self, min_level=350.0, margin=2.0):
        eng = object.__new__(VoskWakeEngine)
        eng._phrases = ["hey odin", "hi odin"]
        eng._sample_rate = self.RATE
        eng._min_level = min_level
        eng._speak_up_margin = margin
        eng._background = None
        eng._recent = []
        eng._peak_blocks = 7
        eng._refractory_s = 0.0
        eng._last_fire = 0.0
        eng.reset = lambda: eng._recent.clear()
        return eng

    def block(self, level, seconds=0.125):
        """A PCM block at roughly the given RMS level."""
        import numpy as np
        return np.full(int(self.RATE * seconds), int(level),
                       dtype=np.int16).tobytes()

    def feed(self, eng, level, seconds):
        for _ in range(max(1, int(seconds / 0.125))):
            eng._track_level(self.block(level))

    def test_a_raised_voice_in_a_quiet_room_wakes(self):
        eng = self.engine()
        self.feed(eng, 20, 3.0)        # quiet room
        self.feed(eng, 3000, 0.5)      # someone speaks
        self.assertIsNotNone(eng._evaluate("hey odin", partial=True))

    def test_a_film_at_its_own_level_does_not_wake(self):
        eng = self.engine()
        self.feed(eng, 1500, 20.0)     # a film, long enough to be background
        self.assertIsNone(eng._evaluate("hey odin", partial=True))

    def test_speaking_over_a_film_still_wakes(self):
        # The case that matters: "hey odin, subtitle" during a film.
        eng = self.engine()
        self.feed(eng, 1500, 20.0)     # film becomes the background
        self.feed(eng, 5000, 0.5)      # and someone talks over it
        self.assertIsNotNone(eng._evaluate("hey odin", partial=True))

    def test_a_whisper_in_a_silent_room_does_not_wake(self):
        # The margin alone would pass this; the absolute floor is what stops
        # a faint sound two rooms away.
        eng = self.engine()
        self.feed(eng, 2, 3.0)
        self.feed(eng, 100, 0.5)
        self.assertIsNone(eng._evaluate("hey odin", partial=True))

    def test_a_few_words_do_not_become_the_background(self):
        # Background rises slowly on purpose: two commands in a row must not
        # raise the bar for the third.
        eng = self.engine()
        self.feed(eng, 20, 3.0)
        for _ in range(3):
            self.feed(eng, 3000, 0.6)
            self.feed(eng, 20, 0.6)
        self.feed(eng, 3000, 0.5)
        self.assertIsNotNone(eng._evaluate("hey odin", partial=True))

    def test_the_gate_can_be_switched_off(self):
        eng = self.engine(min_level=0.0)
        self.feed(eng, 1500, 20.0)
        self.assertIsNotNone(eng._evaluate("hey odin", partial=True))

    def test_background_survives_a_reset(self):
        eng = self.engine()
        self.feed(eng, 1500, 20.0)
        background = eng._background
        eng.reset()
        self.assertEqual(eng._background, background)
        self.assertEqual(eng._recent, [])


class TestListenCleaning(unittest.TestCase):
    def test_unk_tokens_are_dropped(self):
        self.assertEqual(_clean("volume [unk] up"), "volume up")

    def test_whitespace_and_case_normalised(self):
        self.assertEqual(_clean("  Volume   UP  "), "volume up")

    def test_empty_input(self):
        self.assertEqual(_clean(""), "")
        self.assertEqual(_clean("[unk]"), "")


class TestCandidates(unittest.TestCase):
    def test_plain_result_shape(self):
        self.assertEqual(
            candidates_from('{"text": "volume up"}'), ["volume up"])

    def test_nbest_result_shape_keeps_order(self):
        payload = json.dumps({"alternatives": [
            {"text": "cursor", "confidence": 102.4},
            {"text": "pause", "confidence": 102.5},
        ]})
        self.assertEqual(candidates_from(payload), ["cursor", "pause"])

    def test_blanks_and_duplicates_removed(self):
        payload = json.dumps({"alternatives": [
            {"text": "pause"}, {"text": ""}, {"text": "pause"},
            {"text": "[unk]"},
        ]})
        self.assertEqual(candidates_from(payload), ["pause"])

    def test_malformed_payload_is_safe(self):
        self.assertEqual(candidates_from("not json"), [])
        self.assertEqual(candidates_from(""), [])


class TestMatchAny(unittest.TestCase):
    def setUp(self):
        self.table = IntentTable(make_cfg())

    def test_picks_the_first_known_command(self):
        # Exactly the real failure: the top hypothesis is noise from the
        # grammar's bigram model and the real command is second.
        match = self.table.match_any(["cursor", "pause", "half"])
        self.assertIsNotNone(match)
        self.assertEqual(match.intent.name, "media.pause")

    def test_prefers_earlier_candidates(self):
        match = self.table.match_any(["volume up", "volume down"])
        self.assertEqual(match.intent.name, "volume.up")

    def test_no_known_command_returns_none(self):
        self.assertIsNone(self.table.match_any(["the", "is", ""]))
        self.assertIsNone(self.table.match_any([]))


class TestAudioLevel(unittest.TestCase):
    def test_silence_is_zero(self):
        self.assertEqual(rms(np.zeros(800, dtype=np.int16).tobytes()), 0.0)

    def test_empty_block_is_safe(self):
        self.assertEqual(rms(b""), 0.0)

    def test_level_matches_amplitude(self):
        block = (np.ones(800, dtype=np.int16) * 5000).tobytes()
        self.assertAlmostEqual(rms(block), 5000.0, places=3)

    def test_loud_block_does_not_overflow(self):
        # int16 squared overflows a 16-bit accumulator; this must not wrap.
        block = (np.ones(800, dtype=np.int16) * 32767).tobytes()
        self.assertAlmostEqual(rms(block), 32767.0, places=0)


class TestVocabHelpers(unittest.TestCase):
    def test_words_are_split_and_lowercased(self):
        self.assertEqual(words_in(["Volume UP"]), {"volume", "up"})

    def test_meta_tokens_are_skipped(self):
        self.assertEqual(words_in(["[unk]"]), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
