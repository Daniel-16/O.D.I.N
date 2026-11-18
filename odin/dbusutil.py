"""Small blocking D-Bus helpers built on jeepney (pure Python, no C deps).

Used for the two things that have no reliable command-line equivalent on
GNOME Wayland: locking the screen, and taking a screenshot through the
xdg-desktop portal. GNOME denies the older org.gnome.Shell.Screenshot API to
external callers, so the portal is the only working route.
"""

from __future__ import annotations

import logging
import secrets

from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
from jeepney.io.blocking import Proxy, open_dbus_connection

log = logging.getLogger("odin.dbus")


class DBusError(RuntimeError):
    pass


def session_call(
    bus_name: str,
    path: str,
    interface: str,
    member: str,
    signature: str | None = None,
    body: tuple = (),
    timeout: float = 10.0,
):
    """Call a method on the session bus and return the reply body."""
    address = DBusAddress(path, bus_name=bus_name, interface=interface)
    try:
        with open_dbus_connection(bus="SESSION") as conn:
            msg = (
                new_method_call(address, member, signature, body)
                if signature
                else new_method_call(address, member)
            )
            reply = conn.send_and_get_reply(msg, timeout=timeout)
            if reply.header.message_type.name == "error":
                raise DBusError(f"{reply.header.fields.get(4)}: {reply.body}")
            return reply.body
    except DBusError:
        raise
    except Exception as exc:
        raise DBusError(f"{interface}.{member} failed: {exc}") from exc


def portal_screenshot(timeout: float = 30.0) -> str:
    """Take a screenshot via the desktop portal. Returns a file:// URI.

    The Response signal must be matched *before* the method call, otherwise a
    fast compositor can answer before we are listening.
    """
    portal = DBusAddress(
        "/org/freedesktop/portal/desktop",
        bus_name="org.freedesktop.portal.Desktop",
        interface="org.freedesktop.portal.Screenshot",
    )
    token = "odin_" + secrets.token_hex(8)
    try:
        with open_dbus_connection(bus="SESSION") as conn:
            unique = conn.unique_name[1:].replace(".", "_")
            request_path = (
                f"/org/freedesktop/portal/desktop/request/{unique}/{token}"
            )
            rule = MatchRule(
                type="signal",
                interface="org.freedesktop.portal.Request",
                member="Response",
                path=request_path,
            )
            Proxy(message_bus, conn).AddMatch(rule)
            with conn.filter(rule) as queue:
                msg = new_method_call(
                    portal,
                    "Screenshot",
                    "sa{sv}",
                    ("", {"interactive": ("b", False), "handle_token": ("s", token)}),
                )
                reply = conn.send_and_get_reply(msg, timeout=timeout)
                if reply.header.message_type.name == "error":
                    raise DBusError(f"portal refused: {reply.body}")
                response = conn.recv_until_filtered(queue, timeout=timeout)
                code, results = response.body
                if code != 0:
                    raise DBusError(f"screenshot cancelled (code {code})")
                uri = results.get("uri")
                if isinstance(uri, tuple):  # ('s', 'file:///...')
                    uri = uri[1]
                if not uri:
                    raise DBusError("portal returned no file")
                return uri
    except DBusError:
        raise
    except Exception as exc:
        raise DBusError(f"screenshot failed: {exc}") from exc
