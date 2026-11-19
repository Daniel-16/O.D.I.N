"""VLC-specific control, for the things MPRIS does not expose.

MPRIS can play, pause and skip, but it says nothing about subtitles. The next
obvious route - synthesising the "V" keypress - is closed on Wayland: the
compositor refuses synthetic input, and a tool like ydotool would need a root
uinput daemon and would send the key to whatever happens to have focus.

So ODIN asks VLC to run its own hotkey action instead, over VLC's HTTP control
interface on loopback. "key-subtitle-track" is the very action the V key is
bound to, so this is the same code path inside VLC, without needing focus.

The interface has to be enabled once in ~/.config/vlc/vlcrc:

    extraintf=http
    http-host=127.0.0.1
    http-port=8080
    http-password=<something>

`odin check` reports whether that is in place. ODIN never edits vlcrc itself.
"""

from __future__ import annotations

import base64
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import ActionError, Context

log = logging.getLogger("odin.actions.vlc")

VLCRC = Path("~/.config/vlc/vlcrc").expanduser()
NOT_RUNNING = "VLC is not running."
# VLC is up but nothing answers on the control port. The usual cause is a
# second VLC: the first one holds the port, and the second starts with no
# control interface at all ("Failed to create HTTP host" in VLC's log).
NO_INTERFACE = "VLC's web interface is not responding. Run odin check."
NO_PASSWORD = (
    "VLC's web interface has no password. Run odin check."
)
BAD_PASSWORD = "VLC rejected the password. Run odin check."
TIMEOUT_S = 1.5

# VLC's hotkey action behind the V key. No "key-" prefix: VLC adds it.
SUBTITLE_ACTION = "subtitle-track"

_PASSWORD_RE = re.compile(r"^http-password=(.*)$", re.MULTILINE)

# Parsed vlcrc password, keyed by the file's mtime so that changing the
# password does not need a restart of the daemon.
_cached: tuple[float, str] | None = None


def vlcrc_password(path: Path | None = None) -> str:
    """The http-password from vlcrc, or "" if there is none."""
    global _cached
    path = path if path is not None else VLCRC
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    if _cached is not None and _cached[0] == mtime:
        return _cached[1]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = _PASSWORD_RE.search(text)
    password = found.group(1).strip() if found else ""
    _cached = (mtime, password)
    return password


def vlc_is_running() -> bool:
    """True if any VLC process exists, read straight from /proc.

    Used only to tell two failures apart - no VLC at all, versus a VLC whose
    control interface never came up - so that the spoken message points at
    the real problem.
    """
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() == "vlc":
                return True
        except OSError:  # the process exited while we looked
            continue
    return False


def _password(ctx: Context) -> str:
    """The configured password, falling back to the one VLC itself uses."""
    return str(ctx.cfg["vlc"]["password"] or "").strip() or vlcrc_password()


# Built once: ProxyHandler({}) is what keeps a system-wide proxy from
# intercepting a request to our own loopback address.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(ctx: Context, params: dict[str, str]) -> str:
    password = _password(ctx)
    if not password:
        raise ActionError(NO_PASSWORD)

    settings = ctx.cfg["vlc"]
    url = (
        f"http://{settings['host']}:{settings['port']}/requests/status.xml"
        f"?{urllib.parse.urlencode(params)}"
    )
    token = base64.b64encode(f":{password}".encode()).decode()
    request = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
    })

    try:
        with _opener.open(request, timeout=TIMEOUT_S) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ActionError(BAD_PASSWORD) from exc
        raise ActionError(f"VLC returned {exc.code}.") from exc
    except (urllib.error.URLError, OSError) as exc:
        # A refused connection or a timeout. Which of the two messages is
        # right depends on whether VLC itself is up.
        log.debug("VLC request failed: %s", exc)
        message = NO_INTERFACE if vlc_is_running() else NOT_RUNNING
        raise ActionError(message) from exc


def status(ctx: Context) -> str:
    """Fetch VLC's status with no side effect. Used by `odin check`."""
    return _request(ctx, {})


def subtitle(ctx: Context, slots: dict) -> str | None:
    """Cycle the subtitle track, exactly as pressing V in VLC does."""
    _request(ctx, {"command": "key", "val": SUBTITLE_ACTION})
    return None
