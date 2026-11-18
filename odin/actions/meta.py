"""Commands about ODIN itself."""

from __future__ import annotations

from . import ActionError, Context


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


def undo(ctx: Context, slots: dict) -> str | None:
    """Reverse the last reversible change (volume or brightness)."""
    state = ctx.undo
    if not state:
        raise ActionError("There is nothing to undo.")

    kind = state.get("kind")
    if kind == "volume":
        from .volume import restore

        restore(ctx, state)
    elif kind == "brightness":
        from .system import restore_brightness

        restore_brightness(ctx, state)
    else:
        raise ActionError("I cannot undo that.")

    ctx.undo = None  # one level of undo, not a stack
    return f"Back to {state.get('describe', 'where it was')}."


def repeat(ctx: Context, slots: dict) -> str | None:
    """Say the last thing ODIN said."""
    speaker = ctx.speaker
    last = getattr(speaker, "last_said", None) if speaker else None
    if not last:
        raise ActionError("I have not said anything yet.")
    return last


def capabilities(ctx: Context, slots: dict) -> str | None:
    """A short spoken summary - deliberately not the full phrase list."""
    apps = len(ctx.cfg.get("apps", {}))
    folders = len(ctx.cfg.get("folders", {}))
    pages = len(ctx.cfg.get("web", {}))
    return (
        "I can control playback and volume, change the brightness, "
        "take screenshots, lock or suspend the laptop, and tell you the time, "
        f"date or battery. I can also open {apps} apps, {folders} folders "
        f"and {pages} web pages. Say open series, or volume up."
    )
