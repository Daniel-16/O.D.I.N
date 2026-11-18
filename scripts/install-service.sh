#!/usr/bin/env bash
# Install ODIN as a systemd --user service so it is always listening.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
ROOT="$PWD"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/odin.service"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "No virtualenv found. Run scripts/setup.sh first." >&2
    exit 1
fi

mkdir -p "$UNIT_DIR"
sed "s|@ROOT@|$ROOT|g" systemd/odin.service > "$UNIT"
echo "==> Wrote $UNIT"

# GNOME normally imports these already; harmless if they are set twice.
systemctl --user import-environment \
    WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP DBUS_SESSION_BUS_ADDRESS \
    2>/dev/null || true

systemctl --user daemon-reload
systemctl --user enable --now odin.service
echo
systemctl --user --no-pager --lines=0 status odin.service || true
cat <<NOTE

ODIN is now running and will start automatically when you log in.

    systemctl --user status odin     # is it alive
    journalctl --user -u odin -f     # what is it hearing
    systemctl --user restart odin    # after editing config.toml
    systemctl --user stop odin       # silence it for now
    systemctl --user disable odin    # stop it starting at login
NOTE
