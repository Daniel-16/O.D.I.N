"""The always-on daemon: wake word, then one command, then back to waiting."""

from __future__ import annotations

import difflib
import json
import logging
import signal
import threading
import time

from . import intents as intent_mod
from .actions import ActionError, Context, dispatch
from .audio import Microphone
from .config import Config
from .intents import (ALWAYS, CHATTY, IntentTable, confirm_grammar,
                      wake_grammar, wake_phrases)
from .listen import NO_SPEECH, CommandListener
from .speech import ASK, FAIL, OK, WAKE, Speaker
from .wake import VoskWakeEngine

log = logging.getLogger("odin.daemon")

# Grammar used while in standby: only a resume phrase gets through.
_RESUME_GRAMMAR = json.dumps(["start listening", "wake up", intent_mod.UNK])

# Above this many plausible commands, a suggestion is a guess rather than help,
# so ODIN says it did not understand instead. A bare "open" fits ~40 phrases;
# offering two of them at random is worse than admitting it did not catch it.
_MAX_AMBIGUOUS = 4


def load_model(cfg: Config):
    """Load the Vosk acoustic model (a few seconds, ~200 MB resident)."""
    from vosk import Model, SetLogLevel

    SetLogLevel(-1)  # Vosk's own logging is extremely chatty
    if not cfg.model_dir.exists():
        raise SystemExit(
            f"Vosk model missing at {cfg.model_dir}\n"
            "Run scripts/setup.sh to download it."
        )
    log.info("loading speech model: %s", cfg.model_dir.name)
    return Model(str(cfg.model_dir))


class Odin:
    def __init__(self, cfg: Config, model=None, announce: bool = True):
        self.cfg = cfg
        self.announce = announce
        self.model = model or load_model(cfg)
        self.table = IntentTable(cfg)
        # The wake phrases are part of the command grammar on purpose. While a
        # follow-up window is open, saying "hey odin" again reaches the command
        # recogniser; without the phrase in its grammar it has to map those
        # sounds onto *some* command, and was observed turning "hey odin" into
        # "louder" and changing the volume. With it in the grammar the wake
        # word is recognised as itself and treated as a re-wake.
        # Only the real wake phrases - not the decoy words that share the
        # wake grammar.
        self.wake_phrases = set(wake_phrases(cfg))
        # The individual words of those phrases. Half of a wake word ("hey",
        # "odin") reaching the command recogniser is leftover audio, not a
        # command, and must not draw a reply.
        self.wake_words = {
            word for phrase in self.wake_phrases for word in phrase.split()
        }
        self.command_grammar = json.dumps(
            sorted(set(self.table.phrases) | self.wake_phrases)
            + [intent_mod.UNK]
        )
        self.mic = Microphone(
            device=cfg["audio"]["device"] or None,
            sample_rate=cfg["audio"]["sample_rate"],
            block_ms=cfg["audio"]["block_ms"],
        )
        self.speaker = Speaker(cfg, mic=self.mic)
        self.wake = VoskWakeEngine(
            self.model,
            wake_grammar(cfg),
            phrases=wake_phrases(cfg),
            sample_rate=cfg["audio"]["sample_rate"],
            on_partial=cfg["wake"]["on_partial"],
            refractory_s=cfg["wake"]["refractory_s"],
            min_level=cfg["wake"]["min_level"],
            speak_up_margin=cfg["wake"]["speak_up_margin"],
        )
        self.listener = CommandListener(
            self.model, cfg, sample_rate=cfg["audio"]["sample_rate"]
        )
        self.ctx = Context(cfg=cfg, speaker=self.speaker)
        self._stop = threading.Event()

    # -- lifecycle --------------------------------------------------------
    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self.stop())

    def stop(self) -> None:
        log.info("shutting down")
        self._stop.set()

    def run(self) -> None:
        with self.mic:
            if self.announce:
                self.speaker.say("Odin online.")
            log.info("listening for the wake word")
            while not self._stop.is_set():
                block = self.mic.read(timeout=0.5)
                if block is None:
                    continue
                event = self.wake.process(block)
                if event is None:
                    continue
                log.info("wake: %r%s", event.text,
                         " (partial)" if event.partial else "")
                self._handle_wake()
                # Discard anything captured while acting, so the next loop
                # does not re-detect the tail of this interaction.
                self.mic.drain()
                self.wake.reset()

    # -- one interaction --------------------------------------------------
    def _handle_wake(self) -> None:
        self.speaker.beep(WAKE)
        # Let the rest of the wake word go by before listening for a command.
        self.mic.discard_for(float(self.cfg["wake"]["settle_ms"]) / 1000.0)

        follow_up = float(self.cfg["listen"]["follow_up_s"])
        max_turns = max(1, int(self.cfg["listen"]["max_turns"]))

        for turn in range(max_turns):
            keep_going = self._one_turn(
                first=(turn == 0),
                # After acting, wait only briefly for a chained command.
                no_speech_timeout=None if turn == 0 else follow_up,
            )
            if not keep_going or follow_up <= 0:
                return
        log.info("reached the %d-command limit for one wake word", max_turns)

    def _one_turn(self, first: bool, no_speech_timeout: float | None) -> bool:
        """Listen for one command and act on it.

        Returns True to keep the follow-up window open, False to go back to
        waiting for the wake word.
        """
        grammar = _RESUME_GRAMMAR if self.ctx.standby else self.command_grammar
        result = self.listener.listen(
            self.mic, grammar,
            no_speech_timeout=no_speech_timeout,
            threshold_scale=1.0 if first else float(
                self.cfg["listen"]["follow_up_threshold_scale"]),
        )

        if self.cfg["log"]["transcripts"]:
            log.info("heard: %r (%s) candidates=%s",
                     result.text, result.reason, list(result.candidates))

        if not result.ok:
            # Only complain if nothing was ever said; a quiet follow-up
            # window closing is the normal, silent case.
            if first:
                log.info("no command followed the wake word")
                self.speaker.beep(FAIL)
            return False

        candidates = result.candidates or (result.text,)

        # Heard the wake word again rather than a command: start the turn over
        # instead of acting on a misreading of it. Any hypothesis containing a
        # wake phrase counts, not just the top one - live, the wake word came
        # back as ["louder", "hey odin", "odin"] and running "louder" was
        # plainly wrong.
        wake_heard = [c for c in candidates if c in self.wake_phrases]
        if wake_heard:
            log.info("re-woken by %r (hypotheses: %s)",
                     wake_heard[0], list(candidates))
            self.speaker.beep(WAKE)
            self.mic.discard_for(
                float(self.cfg["wake"]["settle_ms"]) / 1000.0)
            return True

        # Part of a wake word and nothing else: the tail of "hey odin", or a
        # stray "hey" picked up while the follow-up window was open. Keep
        # listening without comment.
        if self._is_wake_fragment(candidates[0]):
            log.info("ignoring wake-word fragment %r", candidates[0])
            return True

        # ODIN's own reply coming back through the microphone. It happens
        # when something else is playing through the speakers, which is
        # exactly when a follow-up window is most exposed: after "next" ODIN
        # says "Next.", heard that back, and skipped the track a second time.
        if not first and self._is_own_echo(candidates[0]):
            log.info("ignoring own words heard back: %r", candidates[0])
            return True

        # Nobody addressed ODIN in a follow-up window, so whatever arrived is
        # as likely to be the room as a command. The N-best rescue - keeping
        # the best hypothesis that happens to be a command - is right after
        # the wake word and reckless here: live, it turned "files" into
        # "pause", "viewer" into "dimmer" and "set" into "silence".
        strict = not first and bool(self.cfg["listen"]["follow_up_strict"])
        match = self.table.match_any(
            candidates[:1] if strict else candidates)
        if match is None:
            log.info("no intent matched %r%s", result.text,
                     " (follow-up, top hypothesis only)" if strict else "")
            if strict:
                # Not a command: stop listening rather than give the room
                # four more chances to say something that is.
                log.info("closing the follow-up window")
                return False
            return self._unmatched(result)

        if self.ctx.standby and match.intent.handler != "meta.resume":
            log.info("in standby, ignoring %s", match.intent.name)
            return False

        if match.intent.confirm and self.cfg["system"]["confirm_destructive"]:
            if not self._confirm(match.intent.name):
                self.speaker.say("Cancelled.")
                return True

        self._execute(match)
        return True

    def _is_own_echo(self, text: str) -> bool:
        """True if `text` is nothing but words ODIN itself just said.

        Deliberately narrow: it only fires within `echo_guard_s` of a reply,
        and only when every word heard appears in that reply, so a person
        genuinely repeating a command is not swallowed.
        """
        guard = float(self.cfg["listen"]["echo_guard_s"])
        said = getattr(self.speaker, "last_said", None)
        said_at = float(getattr(self.speaker, "last_said_at", 0.0) or 0.0)
        if guard <= 0 or not said or not said_at:
            return False
        if time.monotonic() - said_at > guard:
            return False
        spoken = {
            word.strip(".,:;!?").lower()
            for word in said.split()
        }
        words = text.split()
        return bool(words) and all(word in spoken for word in words)

    def _is_wake_fragment(self, text: str) -> bool:
        """True if the text is made only of wake-word words."""
        words = text.split()
        return bool(words) and all(word in self.wake_words for word in words)

    # -- clarification ----------------------------------------------------
    def _suggest(self, result) -> list[tuple[str, object]]:
        """Known phrases that are close to what was heard, best first.

        Destructive intents are never suggested: a misheard phrase should not
        be able to put "shut down" in ODIN's mouth.
        """
        cutoff = float(self.cfg["listen"]["clarify_cutoff"])
        phrases = self.table.phrases
        scored: dict[str, float] = {}

        def consider(phrase: str, score: float) -> None:
            if score > scored.get(phrase, 0.0):
                scored[phrase] = score

        for text in (result.candidates or (result.text,)):
            words = text.split()
            if not words:
                continue
            # A command cut short: every word matches the start of a phrase.
            # Scored above 1.0 so truncations beat mere string similarity,
            # with shorter completions preferred.
            for phrase in phrases:
                phrase_words = phrase.split()
                if (len(phrase_words) > len(words)
                        and phrase_words[:len(words)] == words):
                    consider(phrase, 1.0 + 1.0 / len(phrase_words))
            # Misheard rather than cut short.
            for phrase in difflib.get_close_matches(
                    text, phrases, n=4, cutoff=cutoff):
                consider(phrase,
                         difflib.SequenceMatcher(None, text, phrase).ratio())

        if not scored:
            return []

        # Collapse to intents: many phrases map to the same action.
        plausible = {
            match.intent.name
            for phrase in scored
            if (match := self.table.match(phrase)) and not match.intent.confirm
        }
        if len(plausible) > _MAX_AMBIGUOUS:
            log.info("%d plausible commands for %r; not guessing",
                     len(plausible), result.text)
            return []

        options: list[tuple[str, object]] = []
        seen: set[str] = set()
        for phrase in sorted(scored, key=lambda p: scored[p], reverse=True):
            match = self.table.match(phrase)
            if match is None or match.intent.confirm:
                continue
            if match.intent.name in seen:
                continue
            seen.add(match.intent.name)
            options.append((phrase, match))
            if len(options) == 2:
                break
        return options

    def _unmatched(self, result) -> bool:
        """Nothing matched. Offer the nearest thing ODIN does know."""
        if not self.cfg["listen"]["clarify"]:
            self.speaker.beep(FAIL)
            self.speaker.say("I did not get that.")
            return True

        options = self._suggest(result)
        if not options:
            self.speaker.beep(FAIL)
            self.speaker.say("I did not get that.")
            return True

        if len(options) == 1:
            question = f"Did you mean {options[0][0]}?"
        else:
            question = f"Did you mean {options[0][0]}, or {options[1][0]}?"
        log.info("clarifying: %s", question)
        self.speaker.say(question)
        self.speaker.beep(ASK)

        grammar = json.dumps(
            [phrase for phrase, _ in options]
            + ["yes", "no", "cancel", intent_mod.UNK]
        )
        reply = self.listener.listen(self.mic, grammar)
        heard = set(reply.candidates or (reply.text,))
        log.info("clarification reply: %r", reply.text)

        if heard & intent_mod.NO_WORDS:
            self.speaker.say("Never mind.")
            return True
        for phrase, match in options:
            if phrase in heard:
                self._execute(match)
                return True
        # "yes" is only unambiguous when a single option was offered.
        if len(options) == 1 and (heard & intent_mod.YES_WORDS):
            self._execute(options[0][1])
            return True

        self.speaker.beep(FAIL)
        return True

    def _confirm(self, what: str) -> bool:
        log.info("asking for confirmation: %s", what)
        self.speaker.say("Are you sure?")
        self.speaker.beep(ASK)
        reply = self.listener.listen(self.mic, confirm_grammar())
        log.info("confirmation reply: %r", reply.text)
        # Require an explicit yes among the hypotheses, and no competing no.
        heard = set(reply.candidates or (reply.text,))
        return bool(heard & intent_mod.YES_WORDS) and not (
            heard & intent_mod.NO_WORDS
        )

    # -- acting -----------------------------------------------------------
    def _execute(self, match) -> None:
        intent = match.intent
        log.info("running %s %s", intent.name, match.slots or "")
        try:
            spoken = dispatch(intent.handler, self.ctx, match.slots)
        except ActionError as exc:
            log.warning("%s failed: %s", intent.name, exc)
            self.speaker.beep(FAIL)
            self.speaker.say(str(exc))  # failures are always spoken
            return
        except Exception:
            log.exception("%s raised unexpectedly", intent.name)
            self.speaker.beep(FAIL)
            self.speaker.say("Something went wrong.")
            return

        reply = spoken if spoken is not None else intent.reply
        if reply and self._should_speak(intent):
            self.speaker.say(reply)
        else:
            self.speaker.beep(OK)

    def _should_speak(self, intent) -> bool:
        """ALWAYS replies are the answer itself; CHATTY ones are a courtesy."""
        if intent.voice == ALWAYS:
            return True
        if intent.voice == CHATTY:
            return self.cfg["speech"]["verbosity"] == "chatty"
        return False
