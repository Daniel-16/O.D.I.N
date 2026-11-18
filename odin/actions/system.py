"""System actions: lock, suspend, screenshot, brightness, time, power.

Each one uses the route that actually works on GNOME Wayland rather than the
obvious one:
  * lock       - org.gnome.ScreenSaver (loginctl needs a session the daemon
                 does not have)
  * screenshot - xdg-desktop-portal (GNOME denies org.gnome.Shell.Screenshot
                 to external callers)
  * brightness - sysfs, which needs the udev rule from scripts/setup-system.sh;
                 brightnessctl is tried as a fallback
"""

from __future__ import annotations

import datetime as _dt
import logging
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..dbusutil import DBusError, portal_screenshot, session_call
from . import ActionError, Context, run_command

log = logging.getLogger("odin.actions.system")

BACKLIGHT_ROOT = Path("/sys/class/backlight")
MIN_BRIGHTNESS_PERCENT = 5  # never let the voice command black out the screen


# --------------------------------------------------------------------- lock
def lock(ctx: Context, slots: dict) -> str | None:
    try:
        session_call(
            "org.gnome.ScreenSaver",
            "/org/gnome/ScreenSaver",
            "org.gnome.ScreenSaver",
            "Lock",
        )
    except DBusError as exc:
        raise ActionError(f"could not lock the screen: {exc}") from exc
    return None


# ------------------------------------------------------------------- power
def suspend(ctx: Context, slots: dict) -> str | None:
    run_command(["systemctl", "suspend"], timeout=10.0)
    return None


def shutdown(ctx: Context, slots: dict) -> str | None:
    run_command(["systemctl", "poweroff"], timeout=10.0)
    return None


def restart(ctx: Context, slots: dict) -> str | None:
    run_command(["systemctl", "reboot"], timeout=10.0)
    return None


# -------------------------------------------------------------- screenshot
def screenshot(ctx: Context, slots: dict) -> str | None:
    try:
        uri = portal_screenshot()
    except DBusError as exc:
        raise ActionError(f"screenshot failed: {exc}") from exc

    source = Path(unquote(urlparse(uri).path))
    target_dir = ctx.cfg.screenshot_dir
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        target = target_dir / f"Screenshot_{stamp}.png"
        if source.resolve() != target.resolve():
            shutil.move(str(source), str(target))
    except OSError as exc:
        log.warning("could not move screenshot: %s", exc)
        return "Screenshot saved."
    log.info("screenshot saved to %s", target)
    return "Screenshot saved."


# -------------------------------------------------------------- brightness
def _backlight() -> Path:
    if not BACKLIGHT_ROOT.is_dir():
        raise ActionError("this machine has no backlight control")
    devices = sorted(p for p in BACKLIGHT_ROOT.iterdir() if p.is_dir())
    if not devices:
        raise ActionError("this machine has no backlight control")
    return devices[0]


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError) as exc:
        raise ActionError(f"could not read {path.name}") from exc


def _apply_brightness(ctx: Context, target: int) -> str:
    """Set the backlight to an absolute percentage."""
    device = _backlight()
    maximum = _read_int(device / "max_brightness")
    if maximum <= 0:
        raise ActionError("could not read the brightness range")
    target = min(100, max(MIN_BRIGHTNESS_PERCENT, int(target)))
    value = int(round(target * maximum / 100))

    try:
        (device / "brightness").write_text(str(value))
    except PermissionError:
        if shutil.which("brightnessctl") is None:
            raise ActionError(
                "I do not have permission to change the brightness. "
                "Run scripts/setup-system.sh once to fix that."
            ) from None
        run_command(["brightnessctl", "--quiet", "set", f"{target}%"])
    except OSError as exc:
        raise ActionError(f"could not set the brightness: {exc}") from exc

    return f"Brightness {target} percent."


def _step_brightness(ctx: Context, direction: int) -> str | None:
    step = int(ctx.cfg["brightness"]["step"]) * direction
    percent = brightness_percent()
    if percent is None:
        raise ActionError("I could not read the brightness.")
    ctx.undo = {
        "kind": "brightness",
        "percent": percent,
        "describe": f"brightness {percent} percent",
    }
    return _apply_brightness(ctx, percent + step)


def restore_brightness(ctx: Context, state: dict) -> None:
    _apply_brightness(ctx, int(state.get("percent", 50)))


def brightness_up(ctx: Context, slots: dict) -> str | None:
    return _step_brightness(ctx, +1)


def brightness_down(ctx: Context, slots: dict) -> str | None:
    return _step_brightness(ctx, -1)


# ----------------------------------------------------------------- battery
_BATTERY_ROOT = Path("/sys/class/power_supply")
_BATTERY_WORDS = {
    "charging": "charging",
    "discharging": "on battery",
    "not charging": "not charging",
    "full": "full",
}


def battery(ctx: Context, slots: dict) -> str | None:
    for device in sorted(_BATTERY_ROOT.glob("BAT*")):
        capacity = device / "capacity"
        if not capacity.exists():
            continue
        percent = _read_int(capacity)
        raw = ""
        status_file = device / "status"
        if status_file.exists():
            try:
                raw = status_file.read_text().strip().lower()
            except OSError:
                raw = ""
        words = _BATTERY_WORDS.get(raw)
        if words:
            return f"Battery {percent} percent, {words}."
        return f"Battery {percent} percent."
    raise ActionError("I could not find a battery.")


def brightness_percent() -> int | None:
    """Current brightness percentage, for `odin check`."""
    try:
        device = _backlight()
        maximum = _read_int(device / "max_brightness")
        current = _read_int(device / "brightness")
    except ActionError:
        return None
    return round(current * 100 / maximum) if maximum else None


# ------------------------------------------------------------- time & date
def time(ctx: Context, slots: dict) -> str | None:
    now = _dt.datetime.now()
    # %-I drops the leading zero so it reads as "nine" not "oh nine".
    return now.strftime("It is %-I:%M %p.").replace("AM", "A M").replace("PM", "P M")


def date(ctx: Context, slots: dict) -> str | None:
    now = _dt.datetime.now()
    return now.strftime("It is %A, %B %-d.")
