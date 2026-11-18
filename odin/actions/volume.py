"""Output volume via WirePlumber (PipeWire).

`wpctl` is used rather than ALSA mixers because PipeWire owns the sink on this
system; the volume limit keeps "max volume" from pushing past 100% and
clipping.
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


def _set(ctx: Context, spec: str) -> None:
    run_command(["wpctl", "set-volume", "-l", _limit(ctx), SINK, spec])


def current_percent(ctx: Context) -> int | None:
    """Current sink volume as a percentage, or None if it cannot be read."""
    try:
        out = run_command(["wpctl", "get-volume", SINK])
    except ActionError:
        return None
    match = _VOLUME_RE.search(out)
    if not match:
        return None
    return int(round(float(match.group(1)) * 100))


def _report(ctx: Context) -> str | None:
    percent = current_percent(ctx)
    return f"Volume {percent} percent." if percent is not None else None


def up(ctx: Context, slots: dict) -> str | None:
    _set(ctx, f"{_step(ctx)}%+")
    return _report(ctx)


def down(ctx: Context, slots: dict) -> str | None:
    _set(ctx, f"{_step(ctx)}%-")
    return _report(ctx)


def set_level(ctx: Context, slots: dict) -> str | None:
    percent = int(slots.get("percent", 50))
    percent = max(0, min(100, percent))
    _set(ctx, f"{percent}%")
    return f"Volume {percent} percent."


def maximum(ctx: Context, slots: dict) -> str | None:
    _set(ctx, "100%")
    return "Volume at maximum."


def mute(ctx: Context, slots: dict) -> str | None:
    run_command(["wpctl", "set-mute", SINK, "1"])
    return None


def unmute(ctx: Context, slots: dict) -> str | None:
    run_command(["wpctl", "set-mute", SINK, "0"])
    return None
