"""The intent table: the single source of truth for what ODIN understands.

Every phrase ODIN can act on is declared here. Two things are derived from this
one table:

  1. the Vosk *command grammar* - the closed set of phrases the recogniser is
     allowed to return, which is what makes recognition accurate on a laptop
     microphone without a GPU;
  2. the *dispatch table* - phrase text to intent name and slots.

Because both come from the same place, a phrase can never exist in the grammar
without an action behind it, or vice versa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

UNK = "[unk]"

# Spoken number -> value, for "set volume to sixty percent".
NUMBERS: dict[str, int] = {
    "zero": 0,
    "ten": 10,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "one hundred": 100,
}


@dataclass(frozen=True)
class Intent:
    """One thing ODIN can do."""

    name: str
    handler: str  # "module.function" inside odin.actions
    phrases: tuple[str, ...]
    reply: str | None = None  # spoken on success; None = beep only
    confirm: bool = False  # require a spoken "yes" first
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Match:
    intent: Intent
    text: str
    slots: dict[str, Any]


# --------------------------------------------------------------------------
# Fixed intents. Phrase wording is constrained by the Vosk lexicon: every
# word here is verified by `odin check`, so prefer plain words over clever
# ones ("sound on" rather than "unmute", which is not in the lexicon).
# --------------------------------------------------------------------------
_BASE: tuple[Intent, ...] = (
    # ---- media ----------------------------------------------------------
    Intent("media.play", "media.play",
           ("play", "resume", "play music", "continue playing")),
    Intent("media.pause", "media.pause",
           ("pause", "pause music", "pause the music")),
    Intent("media.next", "media.next_track",
           ("next", "next track", "next song", "skip", "skip track")),
    Intent("media.previous", "media.previous_track",
           ("previous", "previous track", "previous song", "go back")),
    Intent("media.stop", "media.stop",
           ("stop", "stop playing", "stop the music")),

    # ---- volume ---------------------------------------------------------
    Intent("volume.up", "volume.up",
           ("volume up", "louder", "turn it up", "turn up the volume",
            "increase volume", "increase the volume")),
    Intent("volume.down", "volume.down",
           ("volume down", "quieter", "turn it down", "turn down the volume",
            "decrease volume", "decrease the volume")),
    Intent("volume.mute", "volume.mute",
           ("mute", "silence", "mute the sound")),
    Intent("volume.unmute", "volume.unmute",
           ("sound on", "volume on", "turn the sound on")),
    Intent("volume.max", "volume.maximum",
           ("max volume", "full volume", "maximum volume")),
    Intent("volume.half", "volume.set_level",
           ("half volume", "set volume to half"), slots={"percent": 50}),

    # ---- system ---------------------------------------------------------
    Intent("system.lock", "system.lock",
           ("lock screen", "lock the screen", "lock my screen"),
           reply="Locking."),
    Intent("system.suspend", "system.suspend",
           ("suspend", "suspend the computer", "put the computer to sleep"),
           reply="Going to sleep."),
    Intent("system.screenshot", "system.screenshot",
           ("screenshot", "take a screenshot", "capture the screen")),
    Intent("system.brightness_up", "system.brightness_up",
           ("brightness up", "brighter", "increase brightness",
            "turn up the brightness")),
    Intent("system.brightness_down", "system.brightness_down",
           ("brightness down", "dimmer", "decrease brightness",
            "turn down the brightness")),
    Intent("system.time", "system.time",
           ("what time is it", "what is the time", "tell me the time")),
    Intent("system.date", "system.date",
           ("what is the date", "what day is it", "what is today")),
    Intent("system.shutdown", "system.shutdown",
           ("shut down", "shut down the computer", "power off"),
           reply="Shutting down.", confirm=True),
    Intent("system.restart", "system.restart",
           ("restart the computer", "reboot", "reboot the computer"),
           reply="Restarting.", confirm=True),

    # ---- meta -----------------------------------------------------------
    Intent("meta.cancel", "meta.cancel",
           ("cancel", "never mind", "forget it", "nothing")),
    Intent("meta.standby", "meta.standby",
           ("stop listening", "go to standby"),
           reply="Standing by."),
    # The only command honoured while in standby, so standby is always
    # recoverable by voice alone.
    Intent("meta.resume", "meta.resume",
           ("start listening", "wake up"),
           reply="Listening."),
    Intent("meta.status", "meta.status",
           ("are you there", "are you awake", "status")),
)


class IntentTable:
    """All intents, the grammar they imply, and text to intent matching."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._intents: list[Intent] = list(_BASE)
        self._intents.extend(self._volume_set_intents())
        self._intents.extend(self._app_intents(cfg["apps"]))
        self._by_phrase: dict[str, tuple[Intent, dict[str, Any]]] = {}
        for intent in self._intents:
            for phrase in intent.phrases:
                key = phrase.lower().strip()
                if key not in self._by_phrase:  # first declaration wins
                    self._by_phrase[key] = (intent, dict(intent.slots))

    # -- construction -----------------------------------------------------
    @staticmethod
    def _volume_set_intents() -> list[Intent]:
        out = []
        for word, value in NUMBERS.items():
            out.append(
                Intent(
                    f"volume.set.{value}",
                    "volume.set_level",
                    (
                        f"set volume to {word} percent",
                        f"set the volume to {word} percent",
                        f"volume {word} percent",
                    ),
                    slots={"percent": value},
                )
            )
        return out

    @staticmethod
    def _app_intents(apps: dict[str, str]) -> list[Intent]:
        out = []
        for alias, desktop_id in apps.items():
            spoken = alias.lower().strip()
            out.append(
                Intent(
                    f"apps.open.{spoken.replace(' ', '_')}",
                    "apps.launch",
                    (f"open {spoken}", f"launch {spoken}", f"start {spoken}"),
                    slots={"app": spoken, "desktop_id": desktop_id},
                )
            )
        return out

    # -- views ------------------------------------------------------------
    @property
    def intents(self) -> list[Intent]:
        return list(self._intents)

    @property
    def phrases(self) -> list[str]:
        return sorted(self._by_phrase)

    def command_grammar(self) -> str:
        return json.dumps(self.phrases + [UNK])

    def match_any(self, texts) -> Match | None:
        """First hypothesis that is a known command.

        Candidates arrive ordered by confidence, so this keeps the most likely
        reading that ODIN can actually act on, and ignores hypotheses that are
        merely grammatical noise.
        """
        for text in texts:
            found = self.match(text)
            if found is not None:
                return found
        return None

    def match(self, text: str) -> Match | None:
        key = " ".join((text or "").lower().split())
        if not key or key == UNK:
            return None
        found = self._by_phrase.get(key)
        if found is None:
            return None
        intent, slots = found
        return Match(intent=intent, text=key, slots=dict(slots))


def wake_grammar(cfg) -> str:
    """Grammar for the always-on wake word recogniser."""
    phrases = [p.lower().strip() for p in cfg["wake"]["phrases"]]
    if cfg["wake"]["allow_bare_name"]:
        phrases.append("odin")
    # Deduplicate while preserving order.
    seen: list[str] = []
    for phrase in phrases:
        if phrase and phrase not in seen:
            seen.append(phrase)
    return json.dumps(seen + [UNK])


def confirm_grammar() -> str:
    return json.dumps(["yes", "no", "cancel", UNK])


YES_WORDS = {"yes"}
NO_WORDS = {"no", "cancel"}
