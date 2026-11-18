"""Output volume via WirePlumber (PipeWire).

`wpctl` is used rather than ALSA mixers because PipeWire owns the sink on this
system; the volume limit keeps "max volume" from pushing past 100% and
clipping. Every change records the previous state first, so "undo that" can put
it back.
"""

from __future__ import annotations

import re

from . import ActionError, Context, run_command

SINK = "@DEFAULT_AUDIO_SINK@"
_VOLUME_RE = re.compile(r"Volume:\s*([0-9.]+)")


def _limit(ctx: Context) -> str:
    return str(float(ctx.cfg["volume"]["limit"]))


def _step(ctx: Context) -> int:
    return int(ctx.cfg["volume"]["step"])


def _read(ctx: Context) -> str | None:
    try:
        return run_command(["wpctl", "get-volume", SINK])
    except ActionError:
        return None


def current_percent(ctx: Context) -> int | None:
    """Current sink volume as a percentage, or None if it cannot be read."""
    out = _read(ctx)
    if out is None:
        return None
    match = _VOLUME_RE.search(out)
    return int(round(float(match.group(1)) * 100)) if match else None


def is_muted(ctx: Context) -> bool | None:
    """True if the sink is muted. wpctl appends [MUTED] to its output."""
    out = _read(ctx)
    return None if out is None else "[MUTED]" in out.upper()


def _snapshot(ctx: Context) -> None:
    """Remember the sink state so the change can be reversed."""
    percent = current_percent(ctx)
    if percent is None:
        return
    muted = bool(is_muted(ctx))
    ctx.undo = {
        "kind": "volume",
        "percent": percent,
        "muted": muted,
        "describe": "muted" if muted else f"volume {percent} percent",
    }


def restore(ctx: Context, state: dict) -> None:
    """Put the sink back to a snapshot taken by _snapshot."""
    percent = int(state.get("percent", 50))
    run_command(["wpctl", "set-volume", "-l", _limit(ctx), SINK, f"{percent}%"])
    run_command(["wpctl", "set-mute", SINK, "1" if state.get("muted") else "0"])


def _set(ctx: Context, spec: str) -> None:
    run_command(["wpctl", "set-volume", "-l", _limit(ctx), SINK, spec])


def _report(ctx: Context) -> str | None:
    percent = current_percent(ctx)
    return f"Volume {percent} percent." if percent is not None else None


def up(ctx: Context, slots: dict) -> str | None:
    _snapshot(ctx)
    _set(ctx, f"{_step(ctx)}%+")
    return _report(ctx)


def down(ctx: Context, slots: dict) -> str | None:
    _snapshot(ctx)
    _set(ctx, f"{_step(ctx)}%-")
    return _report(ctx)


def set_level(ctx: Context, slots: dict) -> str | None:
    percent = int(slots.get("percent", 50))
    percent = 0 if percent < 0 else (100 if percent > 100 else percent)
    _snapshot(ctx)
    _set(ctx, f"{percent}%")
    return f"Volume {percent} percent."


def maximum(ctx: Context, slots: dict) -> str | None:
    _snapshot(ctx)
    _set(ctx, "100%")
    return "Volume at maximum."


def mute(ctx: Context, slots: dict) -> str | None:
    _snapshot(ctx)
    run_command(["wpctl", "set-mute", SINK, "1"])
    return None


def unmute(ctx: Context, slots: dict) -> str | None:
    _snapshot(ctx)
    run_command(["wpctl", "set-mute", SINK, "0"])
    return None


def status(ctx: Context, slots: dict) -> str | None:
    """Answer "am I muted" / "what is the volume"."""
    percent = current_percent(ctx)
    if percent is None:
        raise ActionError("I could not read the volume.")
    if is_muted(ctx):
        return f"The sound is muted, at {percent} percent."
    return f"Volume {percent} percent, not muted."
