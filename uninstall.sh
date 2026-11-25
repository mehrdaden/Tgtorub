#!/usr/bin/env bash
# Remove tg2rub completely (service, files, user). Usage: sudo bash uninstall.sh
set -u
[[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }
read -rp "This deletes /opt/tg2rub (including the Rubika login and pending files). Continue? [y/N] " a
[[ $a == y || $a == Y ]] || exit 0
systemctl disable --now tg2rub tgapi 2>/dev/null
rm -f /etc/systemd/system/tg2rub.service /etc/systemd/system/tgapi.service
systemctl daemon-reload
rm -rf /opt/tg2rub /etc/tg2rub
id tg2rub >/dev/null 2>&1 && userdel tg2rub 2>/dev/null
echo "Removed."
