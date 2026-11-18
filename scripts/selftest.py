"""End-to-end recognition self-test that needs no microphone.

Piper synthesises each phrase, the audio is resampled to 16 kHz and fed
straight into the same wake engine and command grammar the daemon uses. This
proves the grammar, the wake matching and the intent table all line up. It says
nothing about how well ODIN hears *you* - that is what `odin wake-test` is for.

    .venv/bin/python -I scripts/selftest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from odin.config import Config
from odin.intents import IntentTable, wake_grammar
from odin.listen import candidates_from, make_recogniser
from odin.wake import VoskWakeEngine

# Piper renders some isolated one-syllable words far shorter than a person
# says them ("play" comes out at 0.14s against a human's ~0.4s). Below this
# the clip tells us about the synthesiser, not about ODIN, so it is reported
# as a skip rather than counted as a failure.
MIN_FAIR_SECONDS = 0.25

TARGET_RATE = 16000
BLOCK = 2000  # samples per fed block, mirroring the daemon's block size


def _lowpass(audio: np.ndarray, cutoff_hz: float, rate: int,
             taps: int = 101) -> np.ndarray:
    """Windowed-sinc low-pass, applied before downsampling.

    Without this, decimating 22.05 kHz speech to 16 kHz aliases everything
    above 8 kHz back into the band and mangles plosives - which looks exactly
    like a recognition failure but is an artefact of the test harness.
    """
    nyquist = rate / 2.0
    normalised = min(cutoff_hz / nyquist, 0.99)
    n = np.arange(taps) - (taps - 1) / 2.0
    kernel = np.sinc(normalised * n) * np.hamming(taps)
    kernel /= kernel.sum()
    return np.convolve(audio, kernel, mode="same")


def synth(voice, text: str) -> np.ndarray:
    """Synthesise text and resample it to 16 kHz mono int16."""
    chunks = list(voice.synthesize(text))
    audio = np.concatenate([c.audio_int16_array for c in chunks]).astype(np.float64)
    source_rate = chunks[0].sample_rate
    if source_rate != TARGET_RATE:
        # Low-pass below the new Nyquist, then resample.
        audio = _lowpass(audio, TARGET_RATE / 2.0 * 0.9, source_rate)
        duration = audio.size / source_rate
        target_n = int(duration * TARGET_RATE)
        audio = np.interp(
            np.linspace(0.0, duration, target_n, endpoint=False),
            np.linspace(0.0, duration, audio.size, endpoint=False),
            audio,
        )
    return np.clip(audio, -32768, 32767).astype(np.int16)


def feed_wake(engine: VoskWakeEngine, audio: np.ndarray) -> bool:
    # A little leading/trailing silence, as a real utterance would have.
    pad = np.zeros(TARGET_RATE // 2, dtype=np.int16)
    padded = np.concatenate([pad, audio, pad])
    for start in range(0, padded.size, BLOCK):
        if engine.process(padded[start:start + BLOCK].tobytes()) is not None:
            return True
    return False


def recognise(rec, audio: np.ndarray) -> list[str]:
    """Feed audio through the recogniser and return the ranked hypotheses."""
    rec.Reset()
    pad = np.zeros(TARGET_RATE // 2, dtype=np.int16)
    padded = np.concatenate([pad, audio, pad])
    for start in range(0, padded.size, BLOCK):
        rec.AcceptWaveform(padded[start:start + BLOCK].tobytes())
    return candidates_from(rec.FinalResult())


def main() -> int:
    from piper import PiperVoice
    from vosk import KaldiRecognizer, Model, SetLogLevel

    cfg = Config.load()
    for path in (cfg.model_dir, cfg.voice_path):
        if not path.exists():
            print(f"missing {path}; run scripts/setup.sh first")
            return 1

    SetLogLevel(-1)
    print("loading models...")
    model = Model(str(cfg.model_dir))
    voice = PiperVoice.load(str(cfg.voice_path))
    table = IntentTable(cfg)

    failures = 0

    # ---- wake word -------------------------------------------------------
    print("\nwake word")
    engine = VoskWakeEngine(
        model, wake_grammar(cfg), on_partial=cfg["wake"]["on_partial"],
        refractory_s=0.0,
    )
    for phrase in json.loads(wake_grammar(cfg)):
        if phrase.startswith("["):
            continue
        hit = feed_wake(engine, synth(voice, phrase))
        print(f"  {'PASS' if hit else 'FAIL'}  {phrase!r}")
        failures += 0 if hit else 1

    # Things that must NOT wake it.
    print("\nwake word rejects")
    for phrase in ("open the terminal", "what is the weather",
                   "hey google", "play some music"):
        hit = feed_wake(engine, synth(voice, phrase))
        print(f"  {'PASS' if not hit else 'FAIL'}  {phrase!r}"
              f"{' (false trigger)' if hit else ''}")
        failures += 1 if hit else 0

    # ---- commands --------------------------------------------------------
    print("\ncommands")
    rec = make_recogniser(
        model, table.command_grammar(), TARGET_RATE,
        int(cfg["listen"]["alternatives"]),
    )
    samples = [
        ("play", "media.play"),
        ("play music", "media.play"),
        ("pause", "media.pause"),
        ("pause music", "media.pause"),
        ("next track", "media.next"),
        ("previous track", "media.previous"),
        ("volume up", "volume.up"),
        ("volume down", "volume.down"),
        ("mute", "volume.mute"),
        ("sound on", "volume.unmute"),
        ("max volume", "volume.max"),
        ("set volume to forty percent", "volume.set.40"),
        ("open firefox", "apps.open.firefox"),
        ("open spotify", "apps.open.spotify"),
        ("open media player", "apps.open.media_player"),
        ("lock the screen", "system.lock"),
        ("take a screenshot", "system.screenshot"),
        ("brightness up", "system.brightness_up"),
        ("what time is it", "system.time"),
        ("shut down", "system.shutdown"),
        ("stop listening", "meta.standby"),
        ("start listening", "meta.resume"),
    ]
    skipped = 0
    for phrase, expected in samples:
        audio = synth(voice, phrase)
        seconds = audio.size / TARGET_RATE
        heard = recognise(rec, audio)
        match = table.match_any(heard)
        got = match.intent.name if match else None
        ok = got == expected

        if not ok and seconds < MIN_FAIR_SECONDS:
            skipped += 1
            print(f"  SKIP  {phrase!r:34} synthesised clip only "
                  f"{seconds:.2f}s - too short to judge")
            continue

        failures += 0 if ok else 1
        if ok:
            detail = f"heard {match.text!r}"
            # Say so when N-best filtering, not the top hypothesis, saved it.
            if heard and match.text != heard[0]:
                detail += f"   (top hypothesis was {heard[0]!r})"
        else:
            detail = f"heard {heard[0]!r}" if heard else "heard nothing"
            detail += f" -> {got}"
            if len(heard) > 1:
                detail += f"  (alternatives: {', '.join(heard[1:])})"
        print(f"  {'PASS' if ok else 'FAIL'}  {phrase!r:34} {detail}")

    print()
    if failures:
        print(f"{failures} failure(s), {skipped} skipped")
    else:
        print(f"all checks passed ({skipped} skipped as too short to judge)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
