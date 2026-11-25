#!/usr/bin/env bash
# TGTORUB INSTALLER
# tg2rub installer - Ubuntu 22.04 / 24.04.  Run:  sudo bash install.sh
# No compilation, no Docker. Safe to re-run (updates code, keeps your settings and Rubika login).
set -Eeuo pipefail
umask 077
trap 'echo; echo "ERROR on line $LINENO. Fix the problem and run again: sudo bash install.sh" >&2' ERR

[[ $EUID -eq 0 ]] || { echo "Run with sudo:  sudo bash install.sh" >&2; exit 1; }
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT=/opt/tg2rub
APP=$ROOT/app
FILES=(bot.py fileops.py pipeline.py download.py link_download.py nettest.py uploader.py login_rubika.py diag.py requirements.txt)
for f in "${FILES[@]}"; do [[ -f "$HERE/$f" ]] || { echo "Missing $f - extract the whole zip first." >&2; exit 1; }; done

. /etc/os-release
case "${VERSION_ID:-}" in 22.04|24.04) ;; *) echo "WARNING: tested on Ubuntu 22.04/24.04 (you have ${PRETTY_NAME:-unknown}); continuing." ;; esac

say() { printf '\n\033[1;34m>> %s\033[0m\n' "$*"; }
getval() { [[ -f "$1" ]] && grep -E "^$2=" "$1" | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' || true; }

say "1/6  System packages"
export DEBIAN_FRONTEND=noninteractive
need=()
for p in python3 python3-venv python3-pip ca-certificates ffmpeg curl unzip software-properties-common; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || need+=("$p")
done
if ((${#need[@]})); then apt-get update -y; apt-get install -y --no-install-recommends "${need[@]}"; fi
# yt-dlp 2026.10+ requires Python 3.11. Never build this bot's venv with Ubuntu's
# default python3 (3.10 on 22.04, 3.12 on 24.04).
if ! command -v python3.11 >/dev/null 2>&1; then
  case "${VERSION_ID:-}" in
    22.04|24.04)
      say "Installing Python 3.11 from Deadsnakes PPA"
      add-apt-repository -y ppa:deadsnakes/ppa
      apt-get update -y
      apt-get install -y --no-install-recommends python3.11 python3.11-venv
      ;;
    *)
      echo "Python 3.11 is required. Install python3.11 + python3.11-venv, then rerun." >&2
      exit 1
      ;;
  esac
fi
PYTHON_BIN="$(command -v python3.11)"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11), sys.version; print("Using Python", sys.version.split()[0])' 

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
  if [[ -f $APP/.env.bak ]]; then grep -Ev '^(BOT_TOKEN|API_ID|API_HASH|ADMIN_IDS)=' "$APP/.env.bak" | grep -E '^[A-Z][A-Z0-9_]*=' || true
  else printf 'WORK_DIR=%s/work\n' "$ROOT"; fi
} > "$APP/.env.new"
mv "$APP/.env.new" "$APP/.env"
unset BOT_TOKEN API_HASH

say "4/6  Program files and Python packages (no compiling)"
# Build and validate the new environment from the extracted package first.
# Do not replace active source files if PyPI/network installation fails.
NEW_VENV="$APP/.venv-new-$$"
[[ ! -e "$NEW_VENV" ]] || { echo "Temporary venv path already exists: $NEW_VENV" >&2; exit 1; }
"$PYTHON_BIN" -m venv "$NEW_VENV"
PIP=("$NEW_VENV/bin/python" -m pip --disable-pip-version-check)
"${PIP[@]}" install --upgrade pip
ok=0
for i in 1 2 3; do
  if "${PIP[@]}" install --no-warn-script-location --prefer-binary -r "$HERE/requirements.txt"; then ok=1; break; fi
  echo "pip failed (try $i/3), retrying in 5s..."; sleep 5
done
[[ $ok -eq 1 ]] || { echo "pip could not download packages. If PyPI is blocked on this server, set a mirror:  sudo PIP_INDEX_URL=<mirror> bash install.sh" >&2; exit 1; }
"${PIP[@]}" install --no-warn-script-location --only-binary=:all: cryptg >/dev/null 2>&1 && echo "cryptg installed (faster Telegram crypto)" || echo "cryptg skipped (optional)"
"$NEW_VENV/bin/python" -c 'import sys, yt_dlp; assert sys.version_info >= (3, 11); print("Verified Python", sys.version.split()[0], "yt-dlp", yt_dlp.version.__version__)'
"$NEW_VENV/bin/python" -m yt_dlp --version

# Only after the environment passes its checks do we stop the service and
# replace source files. Preserve the previous source and venv for rollback.
systemctl stop tg2rub >/dev/null 2>&1 || true
if [[ -d "$APP" ]] && compgen -G "$APP/*.py" >/dev/null; then
  BACKUP_DIR="$ROOT/backups/source-$(date +%Y%m%d-%H%M%S)"
  install -d -o tg2rub -g tg2rub -m 0700 "$BACKUP_DIR"
  for f in "${FILES[@]}"; do [[ ! -f "$APP/$f" ]] || cp -a "$APP/$f" "$BACKUP_DIR/$f"; done
  echo "Previous program files backed up to: $BACKUP_DIR"
fi
for f in "${FILES[@]}"; do install -o tg2rub -g tg2rub -m 0644 "$HERE/$f" "$APP/$f"; done
VENV_BACKUP=""
if [[ -d "$APP/venv" ]]; then
  install -d -o tg2rub -g tg2rub -m 0700 "$ROOT/backups"
  VENV_BACKUP="$ROOT/backups/venv-$(date +%Y%m%d-%H%M%S)"
  mv "$APP/venv" "$VENV_BACKUP"
fi
if ! mv "$NEW_VENV" "$APP/venv"; then
  [[ -z "$VENV_BACKUP" || ! -d "$VENV_BACKUP" ]] || mv "$VENV_BACKUP" "$APP/venv"
  if [[ -n "${BACKUP_DIR:-}" && -d "$BACKUP_DIR" ]]; then
    for f in "${FILES[@]}"; do
      if [[ -f "$BACKUP_DIR/$f" ]]; then cp -a "$BACKUP_DIR/$f" "$APP/$f"
      else rm -f "$APP/$f"; fi
    done
  fi
  systemctl start tg2rub >/dev/null 2>&1 || true
  echo "Could not activate the new Python environment; previous source and venv were restored where available." >&2
  exit 1
fi
chown -R tg2rub:tg2rub "$ROOT"; chmod 0600 "$APP/.env"
echo "Active venv: $APP/venv (Python 3.11)"
[[ -z "$VENV_BACKUP" ]] || echo "Previous venv backup: $VENV_BACKUP"


# YouTube requires a supported JS runtime. Deno >=2.3 is preferred; an old
# system Deno must not silently disable YouTube challenge solving.
say "4b/6  JavaScript runtime for YouTube"
DENO_OK=0
DENO_CANDIDATE=""
if [[ -x $ROOT/deno/bin/deno ]]; then DENO_CANDIDATE="$ROOT/deno/bin/deno"
elif command -v deno >/dev/null 2>&1; then DENO_CANDIDATE="$(command -v deno)"; fi
if [[ -n $DENO_CANDIDATE ]]; then
  DENO_VER="$("$DENO_CANDIDATE" --version 2>/dev/null | awk 'NR==1 {print $2}')"
  if [[ -n $DENO_VER ]] && dpkg --compare-versions "$DENO_VER" ge 2.3.0; then DENO_OK=1; fi
fi
if [[ $DENO_OK -eq 0 ]]; then
  install -d -o tg2rub -g tg2rub -m 0750 "$ROOT/deno"
  if curl -fsSL --max-time 90 https://deno.land/install.sh -o /tmp/deno-install.sh 2>/dev/null; then
    runuser -u tg2rub -- env DENO_INSTALL="$ROOT/deno" HOME="$ROOT" sh /tmp/deno-install.sh -y --no-modify-path </dev/null >/dev/null 2>&1 \
      || runuser -u tg2rub -- env DENO_INSTALL="$ROOT/deno" HOME="$ROOT" sh /tmp/deno-install.sh </dev/null >/dev/null 2>&1 || true
    rm -f /tmp/deno-install.sh
  fi
  if [[ -x $ROOT/deno/bin/deno ]]; then
    DENO_VER="$("$ROOT/deno/bin/deno" --version 2>/dev/null | awk 'NR==1 {print $2}')"
    if [[ -n $DENO_VER ]] && dpkg --compare-versions "$DENO_VER" ge 2.3.0; then
      echo "Deno ready: $("$ROOT/deno/bin/deno" --version | head -1)"; DENO_OK=1
    fi
  fi
fi
if [[ $DENO_OK -eq 0 ]]; then
  echo "NOTICE: Deno >=2.3 could not be verified. Other sites still work; YouTube may need a compatible Node >=22 runtime or Deno installation."
fi

say "5/6  Services"
# The old Bot API service is no longer used by this version. Preserve its data
# directories (tgdata/tgtmp) instead of deleting user data during an update.
if [[ -f /etc/systemd/system/tgapi.service ]]; then
  systemctl disable --now tgapi >/dev/null 2>&1 || true
  cp -a /etc/systemd/system/tgapi.service "$ROOT/tgapi.service.backup" 2>/dev/null || true
  rm -f /etc/systemd/system/tgapi.service
  systemctl daemon-reload
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
Environment=PATH=$APP/venv/bin:$ROOT/deno/bin:/usr/local/bin:/usr/bin:/bin
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
