#!/usr/bin/env bash
# ODIN system setup. This is the ONLY part that needs root.
#
#   sudo scripts/setup-system.sh
#
# It installs the three system packages ODIN needs and grants this user write
# access to the screen backlight (GNOME exposes no D-Bus API for screen
# brightness, so ODIN writes sysfs directly).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script needs root. Run: sudo $0" >&2
    exit 1
fi

TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" ]]; then
    echo "Could not determine the target user; run with sudo, not as root." >&2
    exit 1
fi

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y libportaudio2 playerctl brightnessctl

RULE=/etc/udev/rules.d/90-odin-backlight.rules
echo "==> Installing backlight access rule ($RULE)"
cat > "$RULE" <<'RULE_EOF'
# Installed by ODIN. Lets the logged-in user adjust screen brightness without
# root. uaccess grants an ACL to the active seat user; the chgrp/chmod lines
# are a fallback for backlight devices that are not seat-assigned.
ACTION=="add", SUBSYSTEM=="backlight", TAG+="uaccess"
ACTION=="add", SUBSYSTEM=="backlight", \
  RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", \
  RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness"
RULE_EOF

echo "==> Adding $TARGET_USER to the video group (fallback path)"
usermod -aG video "$TARGET_USER"

echo "==> Reloading udev"
udevadm control --reload-rules
udevadm trigger --subsystem-match=backlight --action=add
sleep 1

echo
echo "==> Result"
for dev in /sys/class/backlight/*/brightness; do
    [[ -e "$dev" ]] || continue
    ls -l "$dev"
    if sudo -u "$TARGET_USER" test -w "$dev"; then
        echo "    writable by $TARGET_USER: yes"
    else
        echo "    writable by $TARGET_USER: NOT YET - log out and back in,"
        echo "    or ODIN will fall back to brightnessctl."
    fi
done

echo
echo "Done. Now run (as your normal user):  ./odin.sh check"
