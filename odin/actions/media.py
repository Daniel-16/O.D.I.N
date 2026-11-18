"""Media control over MPRIS, via playerctl.

MPRIS is the right layer on Wayland: it talks to the player itself rather than
synthesising media keys, which Wayland does not allow. Spotify, VLC, Chrome and
Firefox all expose it.
"""

from __future__ import annotations

from . import ActionError, Context, run_command

NO_PLAYER = "No media player is running."


def _playerctl(ctx: Context, *args: str) -> str:
    players = ctx.cfg["media"]["players"]
    try:
        return run_command(["playerctl", "--player", players, *args])
    except ActionError as exc:
        if "no players found" in str(exc).lower():
            raise ActionError(NO_PLAYER) from exc
        raise


def play(ctx: Context, slots: dict) -> str | None:
    _playerctl(ctx, "play")
    return None


def pause(ctx: Context, slots: dict) -> str | None:
    _playerctl(ctx, "pause")
    return None


def stop(ctx: Context, slots: dict) -> str | None:
    _playerctl(ctx, "stop")
    return None


def next_track(ctx: Context, slots: dict) -> str | None:
    _playerctl(ctx, "next")
    return None


def previous_track(ctx: Context, slots: dict) -> str | None:
    _playerctl(ctx, "previous")
    return None


def status(ctx: Context, slots: dict) -> str | None:
    return _playerctl(ctx, "status") or None


_MAX_TITLE = 80


def now_playing(ctx: Context, slots: dict) -> str | None:
    """Answer "what is playing" from MPRIS metadata."""
    try:
        raw = _playerctl(
            ctx, "metadata", "--format", "{{status}}|{{title}}|{{artist}}")
    except ActionError as exc:
        if str(exc) == NO_PLAYER:
            return "Nothing is playing."
        raise

    parts = (raw.split("|") + ["", "", ""])[:3]
    state, title, artist = (part.strip() for part in parts)
    if not title:
        return "Nothing is playing."
    if len(title) > _MAX_TITLE:  # stream titles can be enormous
        title = title[:_MAX_TITLE].rstrip() + "..."

    label = f"{title}, by {artist}" if artist else title
    if state.lower() == "playing":
        return f"Playing {label}."
    if state.lower() == "paused":
        return f"Paused: {label}."
    return f"{label}."
