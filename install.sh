#!/usr/bin/env bash
# tg2rub installer - Ubuntu 22.04 / 24.04.  Run:  sudo bash install.sh
# No compilation, no Docker. Safe to re-run (updates code, keeps your settings and Rubika login).
set -Eeuo pipefail
umask 077
trap 'echo; echo "ERROR on line $LINENO. Fix the problem and run again: sudo bash install.sh" >&2' ERR

[[ $EUID -eq 0 ]] || { echo "Run with sudo:  sudo bash install.sh" >&2; exit 1; }
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT=/opt/tg2rub
APP=$ROOT/app
FILES=(bot.py fileops.py pipeline.py download.py link_download.py uploader.py login_rubika.py diag.py requirements.txt)
for f in "${FILES[@]}"; do [[ -f "$HERE/$f" ]] || { echo "Missing $f - extract the whole zip first." >&2; exit 1; }; done

. /etc/os-release
case "${VERSION_ID:-}" in 22.04|24.04) ;; *) echo "WARNING: tested on Ubuntu 22.04/24.04 (you have ${PRETTY_NAME:-unknown}); continuing." ;; esac

say() { printf '\n\033[1;34m>> %s\033[0m\n' "$*"; }
getval() { [[ -f "$1" ]] && grep -E "^$2=" "$1" | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' || true; }

say "1/6  System packages"
export DEBIAN_FRONTEND=noninteractive
need=()
for p in python3 python3-venv python3-pip ca-certificates ffmpeg; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || need+=("$p")
done
if ((${#need[@]})); then apt-get update -y; apt-get install -y --no-install-recommends "${need[@]}"; fi
python3 -c 'import sys; assert sys.version_info >= (3, 8)' || { echo "Python >= 3.8 required" >&2; exit 1; }

say "2/6  User and folders"
id tg2rub >/dev/null 2>&1 || useradd --system --create-home --home-dir "$ROOT" --shell /usr/sbin/nologin tg2rub
install -d -o tg2rub -g tg2rub -m 0700 "$APP" "$ROOT/work"

say "3/6  Settings"
BOT_TOKEN="${BOT_TOKEN:-}"; ADMIN_ID="${ADMIN_ID:-}"; API_ID="${API_ID:-}"; API_HASH="${API_HASH:-}"
OLD_ENV="$APP/.env"; OLD_API=/etc/tg2rub/tgapi.env      # also migrates the previous (Bot API server) version
[[ -z $BOT_TOKEN ]] && BOT_TOKEN="$(getval "$OLD_ENV" BOT_TOKEN)"
[[ -z $ADMIN_ID  ]] && ADMIN_ID="$(getval "$OLD_ENV" ADMIN_IDS)"
[[ -z $API_ID    ]] && API_ID="$(getval "$OLD_ENV" API_ID)"
[[ -z $API_ID    ]] && API_ID="$(getval "$OLD_API" TELEGRAM_API_ID)"
[[ -z $API_HASH  ]] && API_HASH="$(getval "$OLD_ENV" API_HASH)"
[[ -z $API_HASH  ]] && API_HASH="$(getval "$OLD_API" TELEGRAM_API_HASH)"

if [[ -z $BOT_TOKEN ]]; then read -rsp "Bot token (from @BotFather): " BOT_TOKEN; echo; fi
if [[ -z $ADMIN_ID  ]]; then read -rp  "Your numeric Telegram id (from @userinfobot): " ADMIN_ID; fi
if [[ -z $API_ID    ]]; then read -rp  "api_id (from my.telegram.org): " API_ID; fi
if [[ -z $API_HASH  ]]; then read -rsp "api_hash (from my.telegram.org): " API_HASH; echo; fi
BOT_TOKEN="$(echo -n "$BOT_TOKEN" | tr -d '[:space:]')"; ADMIN_ID="$(echo -n "$ADMIN_ID" | tr -d '[:space:]')"
API_ID="$(echo -n "$API_ID" | tr -d '[:space:]')";       API_HASH="$(echo -n "$API_HASH" | tr -d '[:space:]')"
[[ $BOT_TOKEN =~ ^[0-9]{6,}:[A-Za-z0-9_-]{20,}$ ]] || { echo "Bot token format is invalid." >&2; exit 1; }
[[ $ADMIN_ID  =~ ^[0-9]+(,[0-9]+)*$ ]]             || { echo "Admin id must be numeric." >&2; exit 1; }
[[ $API_ID    =~ ^[0-9]+$ ]]                       || { echo "api_id must be numeric." >&2; exit 1; }
[[ $API_HASH  =~ ^[A-Fa-f0-9]{32}$ ]]              || { echo "api_hash must be 32 hex characters." >&2; exit 1; }

if [[ -f $APP/.env ]]; then cp -p "$APP/.env" "$APP/.env.bak"; fi
# keep optional tuning lines from an existing .env, replace the 4 mandatory ones
{
  printf 'BOT_TOKEN=%s\nAPI_ID=%s\nAPI_HASH=%s\nADMIN_IDS=%s\n' "$BOT_TOKEN" "$API_ID" "$API_HASH" "$ADMIN_ID"
  if [[ -f $APP/.env.bak ]]; then grep -Ev '^(BOT_TOKEN|API_ID|API_HASH|ADMIN_IDS)=' "$APP/.env.bak" | grep -E '^[A-Z_]+=' || true
  else printf 'WORK_DIR=%s/work\n' "$ROOT"; fi
} > "$APP/.env.new"
mv "$APP/.env.new" "$APP/.env"
unset BOT_TOKEN API_HASH

say "4/6  Program files and Python packages (no compiling)"
for f in "${FILES[@]}"; do install -o tg2rub -g tg2rub -m 0644 "$HERE/$f" "$APP/$f"; done
[[ -x $APP/venv/bin/python ]] || python3 -m venv "$APP/venv"
PIP=("$APP/venv/bin/python" -m pip --disable-pip-version-check)
ok=0
for i in 1 2 3; do
  if "${PIP[@]}" install --no-warn-script-location --prefer-binary -r "$APP/requirements.txt"; then ok=1; break; fi
  echo "pip failed (try $i/3), retrying in 5s..."; sleep 5
done
[[ $ok -eq 1 ]] || { echo "pip could not download packages. If PyPI is blocked on this server, set a mirror:  sudo PIP_INDEX_URL=<mirror> bash install.sh" >&2; exit 1; }
"${PIP[@]}" install --no-warn-script-location --only-binary=:all: cryptg >/dev/null 2>&1 && echo "cryptg installed (faster Telegram crypto)" || echo "cryptg skipped (optional)"
chown -R tg2rub:tg2rub "$ROOT"; chmod 0600 "$APP/.env"

say "5/6  Services"
# the old version used a local Bot API server; it is not needed any more
rm -rf "$ROOT/tgdata" "$ROOT/tgtmp"
if [[ -f /etc/systemd/system/tgapi.service ]]; then
  systemctl disable --now tgapi >/dev/null 2>&1 || true; rm -f /etc/systemd/system/tgapi.service
fi
cat > /etc/systemd/system/tg2rub.service <<UNIT
[Unit]
Description=Telegram to Rubika file bridge
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Type=simple
User=tg2rub
Group=tg2rub
WorkingDirectory=$APP
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP/venv/bin/python $APP/bot.py
Restart=always
RestartSec=15
TimeoutStopSec=30
LimitNOFILE=65536
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$ROOT
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

say "6/6  Rubika login (one time: phone number + code)"
if ! command -v deno >/dev/null 2>&1; then
  echo "NOTICE: Deno is not installed. YouTube may have limited/failed extraction until Deno >= 2.3 is installed."
  echo "See https://docs.deno.com/runtime/getting_started/installation/"
fi
if ls "$APP"/rubika* >/dev/null 2>&1; then
  echo "Rubika session already exists - skipping login."
elif [[ -t 0 && -z ${SKIP_LOGIN:-} ]]; then
  runuser -u tg2rub -- "$APP/venv/bin/python" "$APP/login_rubika.py"
else
  echo "No terminal for login. Run this yourself, then start the service:"
  echo "  sudo -u tg2rub $APP/venv/bin/python $APP/login_rubika.py"
  echo "  sudo systemctl enable --now tg2rub"
  exit 0
fi

if systemctl is-active --quiet tg2rub; then
  systemctl restart tg2rub
else
  systemctl enable --now tg2rub
fi
sleep 4
systemctl --no-pager --lines=0 status tg2rub || true
cat <<MSG

==================== DONE ====================
Open your bot in Telegram and send  /test   (checks the Rubika connection),
then send any file.

Useful commands:
  journalctl -u tg2rub -f            live log
  sudo systemctl restart tg2rub      restart
  sudo nano $APP/.env                settings (restart after editing)
  sudo bash install.sh               update to a newer package (keeps settings + login)
MSG
