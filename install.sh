
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

trap 'echo "ERROR: Installation failed on line $LINENO" >&2' ERR

# --------------------------------------------------
# Configuration
# --------------------------------------------------

REPO="https://github.com/mehrdaden/Tgtorub.git"
BRANCH="main"

ROOT="/opt/tg2rub"
APP="$ROOT/app"

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

# --------------------------------------------------
# Root check
# --------------------------------------------------

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: Please run this installer as root."
    exit 1
fi

echo "======================================"
echo "       TGTORUB INSTALLER"
echo "======================================"

# --------------------------------------------------
# Install system dependencies
# --------------------------------------------------

export DEBIAN_FRONTEND=noninteractive

apt-get update

apt-get install -y \
    git \
    curl \
    ca-certificates \
    ffmpeg \
    software-properties-common

# --------------------------------------------------
# Install Python 3.11
# --------------------------------------------------

if ! command -v python3.11 >/dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update
fi

apt-get install -y python3.11 python3.11-venv python3.11-dev

python3.11 --version

# --------------------------------------------------
# Check existing configuration
# --------------------------------------------------

if [[ ! -s "$APP/.env" ]]; then
    echo
    echo "ERROR: Existing .env configuration was not found."
    echo "Installation stopped to protect your configuration."
    exit 1
fi

echo "Existing configuration found."

# --------------------------------------------------
# Create service user
# --------------------------------------------------

if ! id tg2rub >/dev/null 2>&1; then
    useradd --system \
        --create-home \
        --home-dir "$ROOT" \
        --shell /usr/sbin/nologin \
        tg2rub
fi

install -d -o tg2rub -g tg2rub -m 750 "$ROOT" "$APP"

# --------------------------------------------------
# Download project from GitHub
# --------------------------------------------------

TEMP_DIR="$(mktemp -d /tmp/tg2rub-install.XXXXXX)"

cleanup() {
    rm -rf "$TEMP_DIR"
}

trap cleanup EXIT

echo
echo "Downloading project from GitHub..."

git clone \
    --depth 1 \
    --branch "$BRANCH" \
    "$REPO" \
    "$TEMP_DIR/source"

SOURCE="$TEMP_DIR/source"

# Verify required files
for file in "${FILES[@]}"; do
    if [[ ! -f "$SOURCE/$file" ]]; then
        echo "ERROR: Required file missing: $file"
        exit 1
    fi
done

echo "Project files verified."

# --------------------------------------------------
# Build new Python environment
# --------------------------------------------------

echo
echo "Creating Python 3.11 environment..."

NEW_VENV="$(mktemp -d "$APP/.venv311.XXXXXX")"

python3.11 -m venv "$NEW_VENV"

"$NEW_VENV/bin/python" -m pip install --upgrade pip

"$NEW_VENV/bin/python" -m pip install \
    -r "$SOURCE/requirements.txt"

# Verify Python
"$NEW_VENV/bin/python" -c \
    'import sys; assert sys.version_info >= (3,11); print(sys.version)'

# Verify yt-dlp
"$NEW_VENV/bin/python" -m yt_dlp --version

echo "Python environment verified."

# --------------------------------------------------
# Stop existing service
# --------------------------------------------------

echo
echo "Stopping existing service..."

systemctl stop tg2rub 2>/dev/null || true

# --------------------------------------------------
# Backup and replace Python environment
# --------------------------------------------------

if [[ -d "$APP/venv" ]]; then
    BACKUP="$APP/venv_backup_$(date +%Y%m%d_%H%M%S)"
    mv "$APP/venv" "$BACKUP"
    echo "Old environment preserved: $BACKUP"
fi

mv "$NEW_VENV" "$APP/venv"

chown -R tg2rub:tg2rub "$APP/venv"

# --------------------------------------------------
# Update application source files
# --------------------------------------------------

echo
echo "Updating application files..."

for file in "${FILES[@]}"; do
    install -o tg2rub -g tg2rub -m 644 \
        "$SOURCE/$file" "$APP/$file"
done

# IMPORTANT:
# .env and session files are intentionally untouched.
# No user data directories are deleted.

chmod 600 "$APP/.env"

# --------------------------------------------------
# Create systemd service
# --------------------------------------------------

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

# --------------------------------------------------
# Start service
# --------------------------------------------------

systemctl daemon-reload
systemctl enable tg2rub
systemctl restart tg2rub

sleep 5

# --------------------------------------------------
# Final result
# --------------------------------------------------

echo
echo "======================================"
echo "         INSTALLATION RESULT"
echo "======================================"

systemctl --no-pager --full status tg2rub || true

echo
echo "Python:"
"$APP/venv/bin/python" --version

echo
echo "yt-dlp:"
"$APP/venv/bin/python" -m yt_dlp --version

echo
echo "Logs command:"
echo "journalctl -u tg2rub -f"

echo
echo "Installation process completed."
