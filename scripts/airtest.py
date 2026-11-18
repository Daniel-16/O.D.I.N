"""Over-the-air test: play the wake word and a command through the speakers
and report what the running daemon actually did.

This is the only test that exercises the whole chain - speaker, room,
microphone, wake word, recogniser, intent, action - so it is what catches
timing problems that look fine in isolation.

    .venv/bin/python -I scripts/airtest.py                     # safe default set
    .venv/bin/python -I scripts/airtest.py "what time is it"

ODIN must be running (systemctl --user start odin). Destructive commands are
refused here: use the unit tests for the confirmation path.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from odin.config import Config

DEFAULT_COMMANDS = [
    "are you there",
    "what time is it",
    "what day is it",
    "volume up",
    "volume down",
]

REFUSED = {"shut down", "shut down the computer", "power off", "reboot",
           "restart the computer", "reboot the computer", "suspend",
           "lock screen", "lock the screen", "lock my screen"}

GAP_S = 1.6       # between the wake word and the command
REPLY_S = 4.0     # time for ODIN to act and reply before we read the journal


def service_running() -> bool:
    out = subprocess.run(["systemctl", "--user", "is-active", "odin"],
                         capture_output=True, text=True)
    return out.stdout.strip() == "active"


def main(argv: list[str]) -> int:
    import sounddevice as sd
    from piper import PiperVoice

    cfg = Config.load()
    commands = argv or DEFAULT_COMMANDS
    refused = [c for c in commands if c.lower() in REFUSED]
    if refused:
        print(f"refusing to trigger over the air: {', '.join(refused)}")
        return 2

    if not service_running():
        print("ODIN is not running. Start it with: systemctl --user start odin")
        return 1

    voice = PiperVoice.load(str(cfg.voice_path))
    wake = cfg["wake"]["phrases"][0]
    # Each command must be preceded by its own wake word, so wait for the
    # follow-up window to close before starting the next one.
    settle_s = REPLY_S + float(cfg["listen"]["follow_up_s"]) + 1.0

    def render(text: str) -> tuple[np.ndarray, int]:
        chunks = list(voice.synthesize(text))
        audio = np.concatenate([c.audio_int16_array for c in chunks])
        return audio, chunks[0].sample_rate

    print(f"\nspeaking {len(commands)} command(s) through the speakers; "
          f"wake word {wake!r}\n")
    failures = 0

    for command in commands:
        wake_audio, rate = render(wake)
        command_audio, _ = render(command)
        clip = np.concatenate([
            np.zeros(int(rate * 0.3), dtype=np.int16),
            wake_audio,
            np.zeros(int(rate * GAP_S), dtype=np.int16),
            command_audio,
            np.zeros(int(rate * 0.3), dtype=np.int16),
        ])

        since = time.strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(1.0)  # keep the journal window clean
        sd.play(clip, samplerate=rate, blocking=True)
        time.sleep(settle_s)

        log = subprocess.run(
            ["journalctl", "--user", "-u", "odin", "--since", since,
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True,
        ).stdout

        # A re-wake counts: if a follow-up window was still open, the wake
        # word is handled there and no "wake:" line is written.
        woke = ("wake:" in log) or ("re-woken" in log)
        heard = ""
        ran = ""
        for line in log.splitlines():
            # Skip the follow-up window closing, which is the last "heard"
            # line of every exchange and says nothing about the command.
            if "heard:" in line and "(no_speech)" not in line:
                heard = line.split("heard:", 1)[1].strip()
            if "running " in line:
                ran = line.split("running ", 1)[1].strip()

        ok = woke and bool(ran)
        failures += 0 if ok else 1
        status = "PASS" if ok else "FAIL"
        detail = f"woke={woke}"
        if heard:
            detail += f"  heard={heard}"
        if ran:
            detail += f"  ran={ran}"
        print(f"  {status}  {command!r:24} {detail}")

    print()
    print("all over-the-air checks passed" if not failures
          else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
