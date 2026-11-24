
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/opt/tg2rub"
APP="$ROOT/app"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: Run as root."
    exit 1
fi

echo "=== Tgtorub installer ==="

# System packages
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y software-properties-common ca-certificates git curl

# Python 3.11
if ! command -v python3.11 >/dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update
    apt-get install -y python3.11 python3.11-venv python3.11-dev
fi

apt-get install -y ffmpeg

python3.11 --version

# Service user
if ! id tg2rub >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$ROOT" --shell /usr/sbin/nologin tg2rub
fi

install -d -o tg2rub -g tg2rub -m 750 "$ROOT" "$APP"

# Stop service if it already exists
systemctl stop tg2rub 2>/dev/null || true

# Preserve existing configuration and sessions.
# Copy only application source files.
for file in bot.py uploader.py link_download.py download.py \
            fileops.py pipeline.py login_rubika.py diag.py requirements.txt; do
    if [[ ! -f "$SOURCE/$file" ]]; then
        echo "ERROR: Missing project file: $file"
        exit 1
    fi
    install -o tg2rub -g tg2rub -m 644 "$SOURCE/$file" "$APP/$file"
done

# Create a fresh Python 3.11 environment.
NEW_VENV="$APP/venv311_new"
rm -rf "$NEW_VENV"

python3.11 -m venv "$NEW_VENV"
"$NEW_VENV/bin/python" -m pip install --upgrade pip
"$NEW_VENV/bin/pip" install -r "$APP/requirements.txt"

# Verify Python and yt-dlp
"$NEW_VENV/bin/python" -c 'import sys; assert sys.version_info >= (3,11)'
"$NEW_VENV/bin/python" -m yt_dlp --version

# Replace old virtual environment, keeping a backup.
if [[ -d "$APP/venv" ]]; then
    BACKUP="$APP/venv_backup_$(date +%Y%m%d_%H%M%S)"
    mv "$APP/venv" "$BACKUP"
    echo "Previous environment saved at: $BACKUP"
fi

mv "$NEW_VENV" "$APP/venv"

chown -R tg2rub:tg2rub "$APP/venv"
chown tg2rub:tg2rub "$APP"/*.py "$APP/requirements.txt"

# Create systemd service
cat > /etc/systemd/system/tg2rub.service <<'SERVICE'
[Unit]
Description=Telegram to Rubika file bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tg2rub
Group=tg2rub
WorkingDirectory=/opt/tg2rub/app
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/tg2rub/app/venv/bin/python /opt/tg2rub/app/bot.py
Restart=always
RestartSec=15
TimeoutStopSec=30
LimitNOFILE=65536
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/tg2rub

[Install]
WantedBy=multi-user.target
SERVICE

chmod 600 "$APP/.env" 2>/dev/null || true

systemctl daemon-reload
systemctl enable tg2rub
systemctl restart tg2rub

echo
echo "=== Installation completed ==="
systemctl --no-pager --full status tg2rub || true
