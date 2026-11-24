
# Tgtorub
## Telegram to Rubika File Transfer Bot

ربات انتقال فایل و لینک بین تلگرام و روبیکا

---

## 1. Requirements

- Ubuntu 22.04 or 24.04
- Root access
- Internet connection
- Telegram account
- Rubika account

## 2. Telegram Credentials

### 2.1 Create a Telegram Bot

1. وارد تلگرام شوید.
2. به @BotFather مراجعه کنید.
3. دستور /newbot را ارسال کنید.
4. نام و username ربات را انتخاب کنید.
5. مقدار Bot Token را ذخیره کنید.

### 2.2 Get API ID and API Hash

1. وارد سایت زیر شوید:

https://my.telegram.org

2. با شماره تلفن تلگرام وارد شوید.
3. وارد API development tools شوید.
4. یک Application بسازید.
5. مقادیر API ID و API Hash را ذخیره کنید.

### 2.3 Get Telegram User ID

به @userinfobot مراجعه کنید و شناسه عددی تلگرام خود را دریافت کنید.

---

## 3. Installation

با SSH وارد سرور شوید و دسترسی Root بگیرید:

```bash
sudo -i
```

دستور نصب:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install-online.sh)
```

### Installer Features

- Install system dependencies
- Install Python 3.11
- Download project from GitHub
- Create Python virtual environment
- Install Python requirements
- Configure systemd service
- Start the bot

IMPORTANT: The current installer requires an existing .env file. It does not create a fresh configuration or ask for Telegram credentials.

---

## 4. Environment Configuration

Configuration file:

```bash
/opt/tg2rub/app/.env
```

Example:

```env
BOT_TOKEN=YOUR_BOT_TOKEN
API_ID=YOUR_API_ID
API_HASH=YOUR_API_HASH
ADMIN_IDS=YOUR_TELEGRAM_USER_ID
```

Replace the example values with your actual Telegram credentials.

Never upload .env to GitHub.

---

## 5. Rubika Login

If no Rubika session exists, run:

```bash
sudo -u tg2rub /opt/tg2rub/app/venv/bin/python /opt/tg2rub/app/login_rubika.py
```

Follow the login instructions.

After successful login:

```bash
systemctl restart tg2rub
```

---

## 6. Service Management

Check service status:

```bash
systemctl status tg2rub
```

Start:

```bash
systemctl start tg2rub
```

Stop:

```bash
systemctl stop tg2rub
```

Restart:

```bash
systemctl restart tg2rub
```

Enable automatic startup:

```bash
systemctl enable tg2rub
```

Disable automatic startup:

```bash
systemctl disable tg2rub
```

---

## 7. Logs

Live logs:

```bash
journalctl -u tg2rub -f
```

Last 100 lines:

```bash
journalctl -u tg2rub -n 100 --no-pager
```

---

## 8. Update

To download and install the latest version from GitHub:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install-online.sh)
```

The installer:

- Downloads the latest project files.
- Creates a new Python environment.
- Installs dependencies.
- Preserves the existing .env file.
- Preserves existing session files.
- Keeps a backup of the previous Python environment.
- Restarts the service.

Always keep a backup of configuration and session files.

---

## 9. Python and yt-dlp Version

Check Python:

```bash
/opt/tg2rub/app/venv/bin/python --version
```

Check yt-dlp:

```bash
/opt/tg2rub/app/venv/bin/python -m yt_dlp --version
```

The installer uses Python 3.11.

---

## 10. Troubleshooting

### Python 3.10 Error

Check the active Python version:

```bash
/opt/tg2rub/app/venv/bin/python --version
```

### Telegram RPC Errors

Check logs:

```bash
journalctl -u tg2rub -n 100 --no-pager
```

Some Telegram RPC errors may be temporary server-side errors.

### X (Twitter) Download Errors

Possible causes:

- Private account or post
- Suspended account
- Sensitive content restrictions
- No video in the post
- Authentication restrictions

Python 3.11 alone does not bypass these restrictions.

---

## 11. Security

- Never share your Bot Token.
- Never share your API Hash.
- Never upload .env to GitHub.
- Never publish Telegram or Rubika session files.
- Keep backups of your configuration and sessions.

---

## GitHub Repository

https://github.com/mehrdaden/Tgtorub
