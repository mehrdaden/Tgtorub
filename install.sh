
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

trap 'echo "ERROR on line $LINENO. Installation stopped." >&2' ERR

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: Run as root."
    exit 1
fi

ROOT="/opt/tg2rub"
APP="$ROOT/app"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FILES=(
    bot.py
    fileops.py
    pipeline.py
    download.py
    link_download.py
    uploader.py
    login_rubika.py
    diag.py
    requirements.txt
)

echo "=================================="
echo "       Tgtorub Installer"
echo "=================================="

# Check project files
for file in "${FILES[@]}"; do
    if [[ ! -f "$SOURCE/$file" ]]; then
        echo "ERROR: Missing project file: $file"
        exit 1
    fi
done

# System packages
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y software-properties-common ca-certificates curl ffmpeg

# Install Python 3.11
if ! command -v python3.11 >/dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update
    apt-get install -y python3.11 python3.11-venv python3.11-dev
else
    apt-get install -y python3.11-venv python3.11-dev
fi

python3.11 --version

# Create service user
if ! id tg2rub >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$ROOT" --shell /usr/sbin/nologin tg2rub
fi

install -d -o tg2rub -g tg2rub -m 750 "$ROOT" "$APP" "$ROOT/work"

# Read existing configuration
getval() {
    if [[ -f "$1" ]]; then
        grep -E "^$2=" "$1" | head -1 | cut -d= -f2- || true
    fi
}

OLD_ENV="$APP/.env"

BOT_TOKEN="${BOT_TOKEN:-}"
ADMIN_ID="${ADMIN_ID:-}"
API_ID="${API_ID:-}"
API_HASH="${API_HASH:-}"

[[ -n "$BOT_TOKEN" ]] || BOT_TOKEN="$(getval "$OLD_ENV" BOT_TOKEN)"
[[ -n "$ADMIN_ID" ]] || ADMIN_ID="$(getval "$OLD_ENV" ADMIN_IDS)"
[[ -n "$API_ID" ]] || API_ID="$(getval "$OLD_ENV" API_ID)"
[[ -n "$API_HASH" ]] || API_HASH="$(getval "$OLD_ENV" API_HASH)"

# Ask for missing configuration
if [[ -z "$BOT_TOKEN" ]]; then
    read -rsp "Telegram Bot Token: " BOT_TOKEN
    echo
fi

if [[ -z "$ADMIN_ID" ]]; then
    read -rp "Telegram Admin ID: " ADMIN_ID
fi

if [[ -z "$API_ID" ]]; then
    read -rp "Telegram API ID: " API_ID
fi

if [[ -z "$API_HASH" ]]; then
    read -rsp "Telegram API Hash: " API_HASH
    echo
fi

BOT_TOKEN="$(echo -n "$BOT_TOKEN" | tr -d '[:space:]')"
ADMIN_ID="$(echo -n "$ADMIN_ID" | tr -d '[:space:]')"
API_ID="$(echo -n "$API_ID" | tr -d '[:space:]')"
API_HASH="$(echo -n "$API_HASH" | tr -d '[:space:]')"

# Validate configuration
[[ "$BOT_TOKEN" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{20,}$ ]] || {
    echo "Invalid Bot Token"
    exit 1
}

[[ "$ADMIN_ID" =~ ^[0-9]+(,[0-9]+)*$ ]] || {
    echo "Invalid Admin ID"
    exit 1
}

[[ "$API_ID" =~ ^[0-9]+$ ]] || {
    echo "Invalid API ID"
    exit 1
}

[[ "$API_HASH" =~ ^[A-Fa-f0-9]{32}$ ]] || {
    echo "Invalid API Hash"
    exit 1
}

# Backup existing configuration
if [[ -f "$OLD_ENV" ]]; then
    cp -p "$OLD_ENV" "$APP/.env.bak"
fi

# Preserve optional settings
{
    printf 'BOT_TOKEN=%s\n' "$BOT_TOKEN"
    printf 'API_ID=%s\n' "$API_ID"
    printf 'API_HASH=%s\n' "$API_HASH"
    printf 'ADMIN_IDS=%s\n' "$ADMIN_ID"

    if [[ -f "$APP/.env.bak" ]]; then
        grep -Ev '^(BOT_TOKEN|API_ID|API_HASH|ADMIN_IDS)=' "$APP/.env.bak" \
            | grep -E '^[A-Z_]+=' || true
    else
        printf 'WORK_DIR=%s/work\n' "$ROOT"
    fi
} > "$APP/.env.new"

mv "$APP/.env.new" "$APP/.env"
chmod 600 "$APP/.env"

unset BOT_TOKEN API_HASH

# Stop existing service
systemctl stop tg2rub 2>/dev/null || true

# Copy application files
for file in "${FILES[@]}"; do
    install -o tg2rub -g tg2rub -m 644 "$SOURCE/$file" "$APP/$file"
done

# Build a fresh Python 3.11 environment
NEW_VENV="$(mktemp -d "$APP/.venv311.XXXXXX")"

python3.11 -m venv "$NEW_VENV"

"$NEW_VENV/bin/python" -m pip install --upgrade pip
"$NEW_VENV/bin/python" -m pip install -r "$APP/requirements.txt"

# Verify environment
"$NEW_VENV/bin/python" -c 'import sys; assert sys.version_info >= (3,11); print(sys.version)'
"$NEW_VENV/bin/python" -m yt_dlp --version

# Keep old environment as backup
if [[ -d "$APP/venv" ]]; then
    BACKUP="$APP/venv_backup_$(date +%Y%m%d_%H%M%S)"
    mv "$APP/venv" "$BACKUP"
    echo "Old Python environment preserved: $BACKUP"
fi

mv "$NEW_VENV" "$APP/venv"

# Permissions
chown -R tg2rub:tg2rub "$APP"
chmod 600 "$APP/.env"

# Create systemd service
cat > /etc/systemd/system/tg2rub.service <<SERVICE
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
SERVICE

systemctl daemon-reload
systemctl enable tg2rub

# Preserve existing Rubika login
if ls "$APP"/rubika* >/dev/null 2>&1; then
    echo "Existing Rubika session found. Login skipped."
elif [[ -t 0 ]]; then
    runuser -u tg2rub -- "$APP/venv/bin/python" "$APP/login_rubika.py"
else
    echo "Rubika login is required."
    echo "Run: sudo -u tg2rub $APP/venv/bin/python $APP/login_rubika.py"
    exit 0
fi

systemctl restart tg2rub

sleep 4

echo
echo "========== INSTALLATION RESULT =========="
systemctl --no-pager --full status tg2rub || true

echo
echo "Installation finished."
echo "Logs: journalctl -u tg2rub -f"
