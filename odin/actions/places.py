"""Opening folders and web pages.

Both go through `gio open`, which hands the target to whatever the desktop has
registered for it - the file manager for a directory, the default browser for a
URL. That keeps ODIN out of the business of knowing which app should handle
what.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import ActionError, Context, spawn_detached

log = logging.getLogger("odin.actions.places")

# Targets that are URIs rather than paths on disk, so no existence check.
_URI_SCHEMES = ("http://", "https://", "trash://", "mailto:", "file://")


def _open(target: str, label: str) -> None:
    argv = (["gio", "open", target] if shutil.which("gio")
            else ["xdg-open", target])
    spawn_detached(argv, label)
    log.info("opened %s (%s)", label, target)


def open_folder(ctx: Context, slots: dict) -> str | None:
    label = slots.get("label", "that folder")
    raw = slots.get("path")
    if not raw:
        raise ActionError(f"I do not have a folder configured for {label}.")

    if raw.startswith(_URI_SCHEMES):
        _open(raw, label)
        return f"Opening {label}."

    path = Path(raw).expanduser()
    if not path.exists():
        raise ActionError(f"The {label} folder is not there.")
    if not path.is_dir():
        raise ActionError(f"{label} is not a folder.")
    _open(str(path), label)
    return f"Opening {label}."


def open_url(ctx: Context, slots: dict) -> str | None:
    label = slots.get("label", "that page")
    url = slots.get("url")
    if not url:
        raise ActionError(f"I do not have a page configured for {label}.")
    _open(url, label)
    return f"Opening {label}."
