"""Commands about ODIN itself."""

from __future__ import annotations

from . import Context


def cancel(ctx: Context, slots: dict) -> str | None:
    return None


def standby(ctx: Context, slots: dict) -> str | None:
    """Ignore commands until "start listening" is heard."""
    ctx.standby = True
    return None


def resume(ctx: Context, slots: dict) -> str | None:
    ctx.standby = False
    return None


def status(ctx: Context, slots: dict) -> str | None:
    return "I am listening."
