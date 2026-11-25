# TG2RUB — Telegram to Rubika

ربات انتقال فایل از تلگرام به روبیکا؛ دریافت فایل از تلگرام یا لینک اینترنتی، دانلود، مدیریت فایل، فشرده‌سازی و رمزگذاری ZIP، تقسیم فایل‌های بزرگ و ارسال به Saved Messages روبیکا.

**مخزن:** https://github.com/mehrdaden/Tgtorub

> این راهنما برای نسخه‌ای از پروژه نوشته شده که فایل‌های `install.sh`، `install-online.sh`، `requirements.txt` و `env.example` آن در مخزن موجود باشند. نصب واقعی روی همه ارائه‌دهندگان VPS تضمین‌شده نیست؛ در صورت بروز خطا، بخش عیب‌یابی را ببینید.

---

## فهرست مطالب

- [امکانات](#امکانات)
- [پیش‌نیازها](#پیشنیازها)
- [اطلاعات لازم قبل از نصب](#اطلاعات-لازم-قبل-از-نصب)
- [نصب خودکار روی Ubuntu](#نصب-خودکار-روی-ubuntu)
- [نصب از ZIP](#نصب-از-zip)
- [نصب دستی Python 3.11](#نصب-دستی-python-311)
- [تنظیمات](#تنظیمات)
- [مدیریت سرویس](#مدیریت-سرویس)
- [دستورات ربات](#دستورات-ربات)
- [به‌روزرسانی](#بهروزرسانی)
- [پشتیبان‌گیری و بازیابی](#پشتیبانگیری-و-بازیابی)
- [عیب‌یابی](#عیبیابی)
- [اجرای تست‌ها](#اجرای-تستها)
- [حذف پروژه](#حذف-پروژه)
- [امنیت](#امنیت)

---

## امکانات

- دریافت فایل از تلگرام و ارسال به روبیکا
- دانلود لینک مستقیم HTTP/HTTPS
- دانلود از سایت‌های پشتیبانی‌شده توسط `yt-dlp` مانند YouTube، Instagram، X/Twitter و TikTok
- دریافت حداکثر ۵ لینک در یک پیام
- تغییر نام فایل
- ساخت ZIP و ZIP رمزدار با AES-256
- تقسیم فایل به حداکثر ۵۰ قسمت
- تقسیم خودکار فایل‌های بزرگ بر اساس سقف تعیین‌شده
- تلاش مجدد آپلود و تغییر روش ارسال
- تشخیص افت سرعت آپلود و تلاش مجدد
- ابزارهای بررسی اتصال، سرعت، فضای دیسک و فایل‌های موقت

پشتیبانی از سایت‌ها به extractor و محدودیت‌های همان سایت وابسته است و تضمین نمی‌شود همه لینک‌ها قابل دانلود باشند. این پروژه برای دور زدن DRM، paywall یا محدودیت دسترسی غیرمجاز طراحی نشده است.

---

## پیش‌نیازها

- Ubuntu 22.04 یا Ubuntu 24.04 پیشنهاد می‌شود.
- دسترسی `root` یا کاربر دارای `sudo`
- اتصال اینترنت و دسترسی به مخازن Ubuntu و PyPI
- فضای دیسک کافی برای فایل‌های در حال دانلود و پردازش
- حساب Telegram و حساب Rubika
- Python 3.11 یا بالاتر برای محیط مجازی پروژه

**مهم:** نسخه پیش‌فرض Python سیستم‌عامل را تغییر ندهید. نصب‌کننده از Python 3.11 برای ساخت محیط مجازی اختصاصی استفاده می‌کند.

---

## اطلاعات لازم قبل از نصب

### ۱. ساخت ربات تلگرام و دریافت Bot Token

1. در تلگرام به `@BotFather` پیام بدهید.
2. دستور `/newbot` را ارسال کنید.
3. نام و username ربات را انتخاب کنید.
4. توکن ربات را دریافت و محرمانه نگهداری کنید.

### ۲. دریافت API ID و API HASH

1. به https://my.telegram.org بروید.
2. با حساب تلگرام وارد شوید.
3. بخش **API development tools** را باز کنید.
4. یک Application بسازید.
5. مقدارهای `api_id` و `api_hash` را نگه دارید.

### ۳. دریافت شناسه عددی مدیر

از `@userinfobot` در تلگرام شناسه عددی حساب خود را دریافت کنید. برای چند مدیر، شناسه‌ها را با کاما جدا کنید؛ مانند `123456789,987654321`.

---

## نصب خودکار روی Ubuntu

این روش پیشنهادی است. ابتدا به سرور SSH بزنید.

### ۱. به‌روزرسانی Ubuntu و نصب ابزارهای پایه

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y curl wget git unzip nano ca-certificates software-properties-common
```

### ۲. دریافت مخزن

```bash
cd /opt
sudo git clone https://github.com/mehrdaden/Tgtorub.git tg2rub-source
```

اگر پوشه از قبل وجود دارد، ابتدا آن را حذف نکنید؛ برای به‌روزرسانی از بخش «به‌روزرسانی» استفاده کنید.

### ۳. ورود به پوشه پروژه و اجرای نصب‌کننده

```bash
cd /opt/tg2rub-source
sudo bash install.sh
```

نصب‌کننده فایل‌های ضروری پروژه را بررسی می‌کند، بسته‌های سیستمی لازم را نصب می‌کند، در صورت نیاز Python 3.11 را از Deadsnakes PPA نصب می‌کند، محیط مجازی می‌سازد، وابستگی‌ها را نصب و سرویس systemd را ایجاد می‌کند.

در زمان نصب، اطلاعات Bot Token، شناسه مدیر، API ID و API HASH درخواست می‌شود.

### ۴. ورود اولیه به روبیکا

نصب‌کننده در صورت نبود نشست قبلی، ورود روبیکا را اجرا می‌کند. شماره تلفن و کد تأیید را در ترمینال وارد کنید.

پس از اتمام نصب، وضعیت سرویس را بررسی کنید:

```bash
sudo systemctl status tg2rub
```

---

## نصب از ZIP

اگر نمی‌خواهید از Git استفاده کنید:

### ۱. نصب ابزارها

```bash
sudo apt update
sudo apt install -y curl unzip
```

### ۲. دانلود ZIP شاخه main

```bash
cd /opt
sudo wget https://github.com/mehrdaden/Tgtorub/archive/refs/heads/main.zip -O tg2rub.zip
```

### ۳. استخراج فایل

```bash
sudo unzip -o tg2rub.zip
```

### ۴. ورود به پوشه و نصب

```bash
cd /opt/Tgtorub-main
sudo bash install.sh
```

**نکته:** اگر نام پوشه استخراج‌شده متفاوت بود، ابتدا با `ls -la /opt` نام واقعی پوشه را پیدا کنید.

---

## نصب دستی Python 3.11

نصب‌کننده اصلی تلاش می‌کند Python 3.11 را در Ubuntu 22.04 و 24.04 نصب کند. فقط اگر این مرحله شکست خورد، مراحل زیر را اجرا کنید.

### ۱. نصب ابزار افزودن مخزن

```bash
sudo apt update
sudo apt install -y software-properties-common
```

### ۲. افزودن Deadsnakes PPA

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
```

### ۳. نصب Python و ماژول venv

```bash
sudo apt install -y python3.11 python3.11-venv
```

### ۴. بررسی نسخه

```bash
python3.11 --version
```

خروجی باید با `Python 3.11` شروع شود.

### ۵. اجرای دوباره نصب‌کننده

به پوشه پروژه برگردید و اجرا کنید:

```bash
cd /opt/tg2rub-source
sudo bash install.sh
```

**هشدار:** از `update-alternatives` برای جایگزین‌کردن Python پیش‌فرض Ubuntu استفاده نکنید.

---

## تنظیمات

فایل تنظیمات پس از نصب در این مسیر قرار می‌گیرد:

```text
/opt/tg2rub/app/.env
```

برای ویرایش:

```bash
sudo nano /opt/tg2rub/app/.env
```

پس از ذخیره تغییرات، سرویس را راه‌اندازی مجدد کنید:

```bash
sudo systemctl restart tg2rub
```

### متغیرهای ضروری

| متغیر | توضیح |
|---|---|
| `BOT_TOKEN` | توکن ربات از BotFather |
| `API_ID` | شناسه API تلگرام |
| `API_HASH` | کلید API تلگرام |
| `ADMIN_IDS` | شناسه عددی مدیر یا مدیران، جداشده با کاما |

### متغیرهای اختیاری

مقادیر پیش‌فرض زیر از فایل `env.example` نسخه پروژه گرفته شده‌اند:

| متغیر | پیش‌فرض | توضیح |
|---|---:|---|
| `WORK_DIR` | `/opt/tg2rub/work` | مسیر فایل‌های کاری |
| `MAX_FILE_GB` | `4` | سقف حجم فایل |
| `RUBIKA_MAX_MB` | `1900` | اندازه هدف هر قسمت آپلود |
| `UPLOAD_ATTEMPTS` | `10` | تعداد تلاش مجدد هر قسمت |
| `UPLOAD_MODE` | `auto` | حالت ارسال: `auto`، `bin` یا `zip` |
| `UPLOAD_TIMEOUT_MIN` | `240` | زمان مجاز هر تلاش آپلود، دقیقه |
| `DOWNLOAD_WORKERS` | `4` | اتصال‌های موازی دانلود تلگرام |
| `AUTO_UPLOAD` | `0` | شروع خودکار آپلود پس از دانلود؛ ۱ فعال |
| `AUTO_DELETE` | `1` | حذف فایل از سرور پس از آپلود موفق؛ ۱ فعال |
| `ZIP_LEVEL` | `0` | سطح فشرده‌سازی ZIP از ۰ تا ۹ |
| `STALE_HOURS` | `24` | عمر فایل‌های رهاشده پیش از پاک‌سازی |
| `LINK_CONNECTIONS` | `4` | اتصال‌های موازی لینک مستقیم |
| `YTDLP_MAX_HEIGHT` | `1080` | حداکثر ارتفاع ویدئو؛ صفر یعنی بهترین کیفیت موجود |
| `LINK_PROXY` | خالی | پراکسی دانلود لینک، در صورت نیاز |
| `COOKIES_FILE` | خالی | مسیر فایل کوکی Netscape، در صورت نیاز |
| `MIN_UPLOAD_KBPS` | `60` | حداقل سرعت قابل قبول آپلود؛ صفر یعنی غیرفعال |
| `STALL_WINDOW_SEC` | `90` | بازه بررسی افت سرعت |
| `STALL_GRACE_SEC` | `45` | مهلت اولیه قبل از بررسی سرعت |
| `MAX_STALL_ABORTS` | `3` | تعداد لغو تلاش به دلیل کندی برای هر قسمت |
| `UPLOAD_STREAMS` | `1` | تعداد آپلود هم‌زمان |
| `RUBIKA_TARGET` | `me` | مقصد؛ `me` یعنی Saved Messages |

### نمونه تنظیم پراکسی دانلود

برای HTTP:

```env
LINK_PROXY=http://127.0.0.1:8080
```

برای SOCKS5:

```env
LINK_PROXY=socks5://127.0.0.1:1080
```

`LINK_PROXY` مربوط به مسیر دانلود لینک است و به معنی عبور آپلود روبیکا از پراکسی نیست.

### نمونه فایل کوکی

```env
COOKIES_FILE=/opt/tg2rub/cookies.txt
```

فایل باید در قالب Netscape cookies باشد. کوکی‌ها محرمانه‌اند و نباید در GitHub قرار بگیرند.

---

## مدیریت سرویس

### مشاهده وضعیت

```bash
sudo systemctl status tg2rub
```

### شروع سرویس

```bash
sudo systemctl start tg2rub
```

### توقف سرویس

```bash
sudo systemctl stop tg2rub
```

### راه‌اندازی مجدد

```bash
sudo systemctl restart tg2rub
```

### فعال‌سازی اجرای خودکار پس از روشن‌شدن سرور

```bash
sudo systemctl enable tg2rub
```

### بررسی فعال بودن سرویس

```bash
sudo systemctl is-active tg2rub
```

---

## دستورات ربات

دستورات زیر در کد پروژه تعریف شده‌اند. دسترسی بعضی دستورات به مدیر محدود است.

| دستور | کاربرد |
|---|---|
| `/start` | شروع و نمایش راهنما |
| `/help` | راهنمای ربات |
| `/test` | تست ارتباط روبیکا |
| `/speed` | تست سرعت |
| `/speed full` | تست کامل‌تر سرعت و اتصال |
| `/diag` | اطلاعات عیب‌یابی |
| `/list` | فهرست فایل‌های موجود |
| `/disk` | فضای دیسک |
| `/clean` | پاک‌سازی فایل‌های کاری |
| `/ytdlp_update` | به‌روزرسانی yt-dlp |
| `/cancel` | لغو عملیات یا ورودی در انتظار |

قبل از اجرای `/clean` مطمئن شوید فایل موردنیاز یا عملیات فعال ندارید.

---

## به‌روزرسانی

**روش پیشنهادی:** نسخه جدید را از ZIP رسمی GitHub دریافت کنید، استخراج کنید و نصب‌کننده را اجرا کنید. نصب‌کننده فعلی از مسیر نصب `/opt/tg2rub` استفاده می‌کند و تنظیمات `.env` و نشست روبیکا را حفظ می‌کند. همچنین از فایل‌های برنامه و محیط مجازی قبلی نسخه پشتیبان می‌گیرد.

### ۱. تهیه پشتیبان پیش از به‌روزرسانی

```bash
sudo mkdir -p /opt/tg2rub/manual-backup
sudo cp -a /opt/tg2rub/app/.env /opt/tg2rub/manual-backup/
sudo cp -a /opt/tg2rub/app/rubika* /opt/tg2rub/manual-backup/ 2>/dev/null || true
```

### ۲. دریافت نسخه جدید

```bash
cd /opt
sudo rm -f Tgtorub-main.zip
sudo wget https://github.com/mehrdaden/Tgtorub/archive/refs/heads/main.zip -O Tgtorub-main.zip
```

### ۳. استخراج نسخه جدید

```bash
sudo rm -rf /opt/Tgtorub-main
sudo unzip -o /opt/Tgtorub-main.zip -d /opt
```

### ۴. اجرای نصب‌کننده نسخه جدید

```bash
cd /opt/Tgtorub-main
sudo bash install.sh
```

نصب‌کننده ابتدا محیط Python جدید و وابستگی‌ها را آماده و بررسی می‌کند؛ سپس سرویس را متوقف کرده و فایل‌های برنامه را جایگزین می‌کند. نسخه قبلی فایل‌های برنامه و محیط مجازی در مسیر backups نگهداری می‌شود.

### ۵. بررسی نتیجه

```bash
sudo systemctl status tg2rub
```

```bash
sudo journalctl -u tg2rub -n 100 --no-pager
```

**مهم:** دستور `rm -rf /opt/Tgtorub-main` فقط پوشه استخراج‌شده نسخه قبلی را حذف می‌کند، نه مسیر داده‌های فعال `/opt/tg2rub`. مسیر نصب فعال را دستی حذف نکنید.

### نصب آنلاین

در صورت کامل‌بودن فایل‌های مخزن و دسترسی به GitHub می‌توان از نصب‌کننده آنلاین استفاده کرد:

```bash
curl -fsSL https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install-online.sh -o install-online.sh
```

```bash
bash install-online.sh
```

این اسکریپت ZIP شاخه `main` را دریافت می‌کند و `install.sh` داخل آن را اجرا می‌کند.

---

## پشتیبان‌گیری و بازیابی

اطلاعات مهم پروژه در مسیر `/opt/tg2rub` قرار دارد. مهم‌ترین موارد:

- `/opt/tg2rub/app/.env`
- فایل نشست روبیکا در مسیر برنامه
- `/opt/tg2rub/work`
- نسخه‌های پشتیبان نصب‌کننده در `/opt/tg2rub/backups`

### تهیه پشتیبان کامل

```bash
sudo tar -czf /root/tg2rub-backup-$(date +%F-%H%M).tar.gz /opt/tg2rub /etc/systemd/system/tg2rub.service
```

### مشاهده فایل‌های پشتیبان

```bash
sudo ls -lh /root/tg2rub-backup-*.tar.gz
```

### بازیابی

ابتدا سرویس را متوقف کنید:

```bash
sudo systemctl stop tg2rub
```

سپس فایل پشتیبان موردنظر را استخراج کنید:

```bash
sudo tar -xzf /root/tg2rub-backup-YYYY-MM-DD-HHMM.tar.gz -C /
```

به‌جای نام نمونه، نام واقعی فایل پشتیبان را وارد کنید.

سپس:

```bash
sudo systemctl daemon-reload
sudo systemctl restart tg2rub
```

---

## مشاهده لاگ‌ها و عیب‌یابی

### مشاهده زنده لاگ‌ها

```bash
sudo journalctl -u tg2rub -f
```

### مشاهده ۱۰۰ خط آخر

```bash
sudo journalctl -u tg2rub -n 100 --no-pager
```

### مشاهده لاگ‌های بوت جاری

```bash
sudo journalctl -u tg2rub -b --no-pager
```

### بررسی وضعیت فضای دیسک

```bash
df -h
```

### بررسی فضای پوشه پروژه

```bash
sudo du -sh /opt/tg2rub
```

### بررسی نسخه Python

```bash
python3.11 --version
```

### بررسی Python محیط مجازی

```bash
sudo -u tg2rub /opt/tg2rub/app/venv/bin/python --version
```

### بررسی yt-dlp

```bash
sudo -u tg2rub /opt/tg2rub/app/venv/bin/yt-dlp --version
```

### بررسی Deno

```bash
sudo -u tg2rub /opt/tg2rub/deno/bin/deno --version
```

اگر Deno در مسیر بالا وجود نداشت:

```bash
deno --version
```

---

## عیب‌یابی خطاهای رایج

### ۱. خطای `python3.11: command not found`

Python 3.11 نصب نشده است.

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv
```

سپس نصب‌کننده را دوباره اجرا کنید.

### ۲. خطای `No module named venv`

```bash
sudo apt install -y python3.11-venv
```

### ۳. خطای `No module named ...`

احتمالاً وابستگی‌ها در محیط مجازی نصب نشده‌اند یا سرویس از Python اشتباه استفاده می‌کند.

```bash
sudo /opt/tg2rub/app/venv/bin/python -m pip install -r /opt/tg2rub/app/requirements.txt
```

سپس:

```bash
sudo systemctl restart tg2rub
```

### ۴. خطای `pip could not download packages`

علت‌های احتمالی:

- اختلال اینترنت سرور
- مسدودبودن PyPI
- DNS نامناسب
- اختلال موقت مخزن بسته‌ها

بررسی اتصال:

```bash
curl -I https://pypi.org
```

در صورت نیاز می‌توانید از Mirror معتبر PyPI استفاده کنید:

```bash
sudo PIP_INDEX_URL=https://pypi.org/simple bash install.sh
```

اگر نصب‌کننده از مسیر دیگری اجرا می‌شود، ابتدا وارد همان پوشه پروژه شوید.

### ۵. خطای `Telegram bot login failed`

موارد زیر را بررسی کنید:

- صحت Bot Token
- صحت API ID و API HASH
- دسترسی سرور به Telegram
- وضعیت سرویس تلگرام
- معتبر بودن نشست قبلی

لاگ:

```bash
sudo journalctl -u tg2rub -n 150 --no-pager
```

### ۶. خطای `ImportBotAuthorizationRequest` یا `Telegram is having internal issues`

این خطا می‌تواند ناشی از اختلال موقت Telegram، محدودیت IP یا مشکل نشست باشد.

ابتدا سرویس را Restart کنید:

```bash
sudo systemctl restart tg2rub
```

اگر خطا ادامه داشت، لاگ کامل را بررسی کنید. فایل نشست را بدون پشتیبان‌گیری حذف نکنید.

### ۷. خطای `Deprecated Feature: Support for Python version ...`

نسخه Python محیط مجازی قدیمی است.

```bash
/opt/tg2rub/app/venv/bin/python --version
```

اگر نسخه کمتر از 3.11 است، نصب‌کننده نسخه جدید را اجرا کنید:

```bash
cd /opt/Tgtorub-main
sudo bash install.sh
```

### ۸. خطای `Deno` یا YouTube JavaScript challenge

نسخه Deno باید 2.3 یا بالاتر باشد.

```bash
/opt/tg2rub/deno/bin/deno --version
```

اگر Deno نصب نشده یا نسخه آن قدیمی است، نصب‌کننده را مجدداً اجرا کنید. برخی سایت‌ها ممکن است به Node.js نسخه 22 یا بالاتر یا تنظیمات دسترسی دیگری نیاز داشته باشند.

### ۹. خطای HTTP 403 هنگام دانلود

دلایل احتمالی:

- محدودیت IP سرور
- نیاز به ورود مجاز
- محدودیت منطقه‌ای
- منقضی‌شدن کوکی
- محدودیت‌های سایت مقصد

از کوکی معتبر حساب خودتان در صورت داشتن مجوز استفاده کنید. تغییر IP یا استفاده از پراکسی فقط در صورت مجازبودن و سازگاری با سایت مقصد انجام شود.

### ۱۰. خطای Rubika هنگام آپلود

موارد زیر را بررسی کنید:

- اتصال اینترنت سرور
- معتبر بودن نشست روبیکا
- فضای آزاد دیسک
- حجم فایل
- لاگ سرویس

```bash
sudo journalctl -u tg2rub -n 150 --no-pager
```

سپس از داخل ربات دستور زیر را اجرا کنید:

```text
/test
```

### ۱۱. سرویس بعد از نصب فعال نمی‌شود

```bash
sudo systemctl daemon-reload
sudo systemctl enable tg2rub
sudo systemctl restart tg2rub
sudo systemctl status tg2rub
```

### ۱۲. خطای `Permission denied`

مالکیت مسیر نصب را بررسی کنید:

```bash
sudo chown -R tg2rub:tg2rub /opt/tg2rub
sudo chmod 600 /opt/tg2rub/app/.env
sudo systemctl restart tg2rub
```

### ۱۳. فایل دانلود می‌شود ولی آپلود انجام نمی‌شود

- فضای آزاد دیسک را بررسی کنید.
- وضعیت اتصال روبیکا را با `/test` بررسی کنید.
- لاگ آپلود را مشاهده کنید.
- مقدار `RUBIKA_MAX_MB` را بررسی کنید.
- از اجرای هم‌زمان تعداد زیادی آپلود خودداری کنید.

### ۱۴. سرویس مدام Restart می‌شود

```bash
sudo systemctl status tg2rub
```

```bash
sudo journalctl -u tg2rub -n 200 --no-pager
```

اولین خطای Python یا Import را پیدا کنید؛ Restart مکرر به‌تنهایی علت مشکل را نشان نمی‌دهد.

---

## اجرای تست‌ها

برای اجرای تست‌ها در محیط نصب‌شده:

```bash
cd /opt/tg2rub/app
```

```bash
sudo /opt/tg2rub/app/venv/bin/python -m pytest -v
```

اگر `pytest` نصب نیست:

```bash
sudo /opt/tg2rub/app/venv/bin/python -m pip install pytest
```

سپس دوباره تست را اجرا کنید.

تست‌های خودکار جایگزین تست واقعی ورود تلگرام، ورود روبیکا، دانلود زنده و آپلود واقعی نیستند.

---

## حذف پروژه

**هشدار: حذف کامل، نشست روبیکا، تنظیمات، فایل‌های کاری و نسخه‌های پشتیبان داخل `/opt/tg2rub` را نیز حذف می‌کند. ابتدا از اطلاعات مهم پشتیبان بگیرید.**

در پوشه‌ای که `uninstall.sh` قرار دارد اجرا کنید:

```bash
sudo bash uninstall.sh
```

اسکریپت قبل از حذف، تأیید شما را درخواست می‌کند.

---

## نکات امنیتی

- فایل `.env` را در GitHub آپلود نکنید.
- Bot Token، API HASH، کوکی و نشست روبیکا را در اختیار دیگران قرار ندهید.
- فایل‌های `*.session` و فایل‌های حاوی اطلاعات ورود را عمومی نکنید.
- از رمز قوی برای ZIP رمزدار استفاده کنید.
- دسترسی مدیر را فقط به شناسه‌های مورداعتماد بدهید.
- فایل‌های ناشناس را روی سرور اجرا نکنید.
- قبل از به‌روزرسانی یا حذف، پشتیبان تهیه کنید.
- فایل‌های محرمانه را در گزارش خطا یا اسکرین‌شات عمومی منتشر نکنید.

---

## ساختار پروژه

```text
Tgtorub/
├── bot.py
├── diag.py
├── download.py
├── fileops.py
├── install.sh
├── install-online.sh
├── link_download.py
├── login_rubika.py
├── nettest.py
├── pipeline.py
├── uploader.py
├── uninstall.sh
├── requirements.txt
├── env.example
├── README.md
├── README-FA.md
├── CHANGELOG-v6.md
├── QA-REPORT-FA.md
└── tests/
    ├── test_core.py
    ├── test_flow.py
    ├── test_links.py
    └── test_v4.py
```

---

## گزارش خطا و پیشنهاد

برای گزارش خطا، اطلاعات زیر را ارائه کنید:

1. نسخه Ubuntu
2. نسخه Python
3. دستور اجراشده
4. متن کامل خطا
5. خروجی مرتبط از `journalctl`

قبل از ارسال گزارش، توکن‌ها، شماره تلفن، API HASH، کوکی و اطلاعات نشست را حذف کنید.

---

**TG2RUB — Telegram to Rubika**

Repository: https://github.com/mehrdaden/Tgtorub
