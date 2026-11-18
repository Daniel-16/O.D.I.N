"""The always-on daemon: wake word, then one command, then back to waiting."""

from __future__ import annotations

import json
import logging
import signal
import threading

from . import intents as intent_mod
from .actions import ActionError, Context, dispatch
from .audio import Microphone
from .config import Config
from .intents import IntentTable, confirm_grammar, wake_grammar
from .listen import NO_SPEECH, CommandListener
from .speech import ASK, FAIL, OK, WAKE, Speaker
from .wake import VoskWakeEngine

log = logging.getLogger("odin.daemon")

# Grammar used while in standby: only a resume phrase gets through.
_RESUME_GRAMMAR = json.dumps(["start listening", "wake up", intent_mod.UNK])


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
        self.command_grammar = self.table.command_grammar()
        self.mic = Microphone(
            device=cfg["audio"]["device"] or None,
            sample_rate=cfg["audio"]["sample_rate"],
            block_ms=cfg["audio"]["block_ms"],
        )
        self.speaker = Speaker(cfg, mic=self.mic)
        self.wake = VoskWakeEngine(
            self.model,
            wake_grammar(cfg),
            sample_rate=cfg["audio"]["sample_rate"],
            on_partial=cfg["wake"]["on_partial"],
            refractory_s=cfg["wake"]["refractory_s"],
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
        grammar = _RESUME_GRAMMAR if self.ctx.standby else self.command_grammar
        result = self.listener.listen(self.mic, grammar)

        if self.cfg["log"]["transcripts"]:
            log.info("heard: %r (%s) candidates=%s",
                     result.text, result.reason, list(result.candidates))

        if not result.ok:
            if result.reason == NO_SPEECH:
                log.info("no command followed the wake word")
            self.speaker.beep(FAIL)
            return

        match = self.table.match_any(result.candidates or (result.text,))
        if match is None:
            log.info("no intent matched %r", result.text)
            self.speaker.beep(FAIL)
            self.speaker.say("I did not get that.")
            return

        if self.ctx.standby and match.intent.handler != "meta.resume":
            log.info("in standby, ignoring %s", match.intent.name)
            return

        if match.intent.confirm and self.cfg["system"]["confirm_destructive"]:
            if not self._confirm(match.intent.name):
                self.speaker.say("Cancelled.")
                return

        self._execute(match)

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

    def _execute(self, match) -> None:
        intent = match.intent
        log.info("running %s %s", intent.name, match.slots or "")
        try:
            spoken = dispatch(intent.handler, self.ctx, match.slots)
        except ActionError as exc:
            log.warning("%s failed: %s", intent.name, exc)
            self.speaker.beep(FAIL)
            self.speaker.say(str(exc))
            return
        except Exception:
            log.exception("%s raised unexpectedly", intent.name)
            self.speaker.beep(FAIL)
            self.speaker.say("Something went wrong.")
            return

        reply = spoken if spoken is not None else intent.reply
        if reply:
            self.speaker.say(reply)
        else:
            self.speaker.beep(OK)
