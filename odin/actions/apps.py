"""Launching applications.

Launching is done with gtk-launch on the .desktop id, which lets the desktop
environment start the app exactly as a click would (correct environment,
sandbox and scaling). The process is detached so apps outlive the daemon.

Note: *closing* or focusing windows is deliberately not offered. GNOME on
Wayland does not allow scripted window control, and killing processes by name
risks losing unsaved work.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import ActionError, Context

log = logging.getLogger("odin.actions.apps")


def search_dirs() -> list[Path]:
    """Every directory that may hold .desktop files, in XDG order."""
    dirs: list[Path] = []
    home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser()
    dirs.append(home / "applications")
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for entry in raw.split(":"):
        if entry:
            dirs.append(Path(entry) / "applications")
    # Snap and Flatpak exports are not always present in XDG_DATA_DIRS.
    dirs.append(Path("/var/lib/snapd/desktop/applications"))
    dirs.append(Path("/var/lib/flatpak/exports/share/applications"))
    seen: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return seen


def resolve_desktop(desktop_id: str) -> Path | None:
    """Locate the .desktop file for an id, or None if it is not installed."""
    name = desktop_id if desktop_id.endswith(".desktop") else f"{desktop_id}.desktop"
    for directory in search_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def display_name(path: Path, fallback: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Name="):
                return line.partition("=")[2].strip() or fallback
    except OSError:
        pass
    return fallback


def launch(ctx: Context, slots: dict) -> str | None:
    spoken = slots.get("app", "that")
    desktop_id = slots.get("desktop_id")
    if not desktop_id:
        raise ActionError(f"I do not have an app configured for {spoken}.")

    path = resolve_desktop(desktop_id)
    if path is None:
        raise ActionError(f"{spoken} does not seem to be installed.")
    if shutil.which("gtk-launch") is None:
        raise ActionError("gtk-launch is not installed")

    launch_id = path.stem
    try:
        subprocess.Popen(
            ["gtk-launch", launch_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # survives the daemon
        )
    except OSError as exc:
        raise ActionError(f"could not launch {spoken}: {exc}") from exc

    log.info("launched %s (%s)", spoken, launch_id)
    return f"Opening {display_name(path, spoken)}."
