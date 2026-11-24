Tgtorub

Telegram to Rubika Bridge

ربات انتقال فایل و لینک بین تلگرام و روبیکا

1. پیش‌نیازها

- سرور Ubuntu 22.04 یا 24.04
- دسترسی Root به سرور
- اتصال اینترنت
- حساب تلگرام و روبیکا

2. دریافت اطلاعات تلگرام

ساخت ربات تلگرام

1. وارد تلگرام شوید.
2. به "@BotFather" مراجعه کنید.
3. دستور "/newbot" را ارسال کنید.
4. نام و username ربات را تعیین کنید.
5. مقدار Bot Token را دریافت و ذخیره کنید.

دریافت API ID و API Hash

1. وارد سایت زیر شوید:

https://my.telegram.org

2. با شماره تلگرام وارد شوید.
3. بخش API development tools را باز کنید.
4. یک Application ایجاد کنید.
5. مقادیر API ID و API Hash را ذخیره کنید.

دریافت Telegram User ID

به ربات "@userinfobot" مراجعه کنید و شناسه عددی حساب تلگرام خود را دریافت کنید.

3. آماده‌سازی تنظیمات

نصب‌کننده فعلی برای اجرا به فایل ".env" موجود نیاز دارد و اطلاعات را خودش دریافت نمی‌کند.

فایل تنظیمات باید در مسیر زیر قرار داشته باشد:

"/opt/tg2rub/app/.env"

نمونه ساختار تنظیمات:

API_ID=YOUR_API_ID
API_HASH=YOUR_API_HASH
BOT_TOKEN=YOUR_BOT_TOKEN
OWNER_TELEGRAM_ID=YOUR_TELEGRAM_ID
RUBIKA_SESSION=data/rubika
MAX_FILE_GB=2

مقادیر نمونه را با اطلاعات واقعی خود جایگزین کنید.

توجه: فایل ".env" محرمانه است و نباید در GitHub منتشر شود.

4. نصب خودکار

با SSH و دسترسی Root وارد سرور شوید.

دستور زیر را اجرا کنید:

bash <(curl -fsSL https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install-online.sh)

نصب‌کننده به‌صورت خودکار:

- پیش‌نیازهای سیستم را نصب می‌کند.
- Python 3.11 را نصب می‌کند.
- پروژه را از GitHub دریافت می‌کند.
- محیط مجازی Python را ایجاد می‌کند.
- کتابخانه‌های موردنیاز را نصب می‌کند.
- سرویس systemd را ایجاد و راه‌اندازی می‌کند.
- محیط Python قبلی را در صورت وجود پشتیبان‌گیری می‌کند.

5. ورود به روبیکا

اگر نشست روبیکا از قبل وجود نداشته باشد، ورود باید به‌صورت دستی انجام شود:

sudo -u tg2rub /opt/tg2rub/app/venv/bin/python /opt/tg2rub/app/login_rubika.py

شماره تلفن و کد تأیید را طبق درخواست برنامه وارد کنید.

پس از موفقیت ورود، نشست روبیکا ذخیره می‌شود.

سپس سرویس را راه‌اندازی مجدد کنید:

systemctl restart tg2rub

6. بررسی وضعیت ربات

مشاهده وضعیت سرویس:

systemctl status tg2rub

مشاهده لاگ زنده:

journalctl -u tg2rub -f

مشاهده 100 خط آخر لاگ:

journalctl -u tg2rub -n 100 --no-pager

7. دستورات مدیریت ربات

راه‌اندازی:

systemctl start tg2rub

توقف:

systemctl stop tg2rub

راه‌اندازی مجدد:

systemctl restart tg2rub

فعال‌سازی اجرای خودکار پس از روشن شدن سرور:

systemctl enable tg2rub

8. به‌روزرسانی ربات

برای دریافت آخرین نسخه فایل‌ها از شاخه main در GitHub، دستور نصب‌کننده را دوباره اجرا کنید:

bash <(curl -fsSL https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install-online.sh)

در فرایند به‌روزرسانی:

- فایل‌های برنامه جایگزین می‌شوند.
- وابستگی‌های Python دوباره نصب می‌شوند.
- محیط Python قبلی پشتیبان‌گیری می‌شود.
- فایل ".env" و نشست‌های موجود عمداً حذف نمی‌شوند.
- سرویس پس از نصب مجدداً راه‌اندازی می‌شود.

توجه: از فایل‌های تنظیمات و نشست‌ها نسخه پشتیبان نگهداری کنید.

9. مشکلات متداول

خطای Python 3.10 در yt-dlp

نسخه فعلی نصب‌کننده از Python 3.11 استفاده می‌کند. برای بررسی:

/opt/tg2rub/app/venv/bin/python --version

بررسی نسخه yt-dlp

/opt/tg2rub/app/venv/bin/python -m yt_dlp --version

بررسی خطاهای سرویس

journalctl -u tg2rub -n 200 --no-pager

خطای ورود تلگرام

اگر خطای موقت Telegram RPC دریافت کردید، ابتدا لاگ را بررسی کنید. برخی خطاها ممکن است موقتی و مربوط به سرورهای تلگرام باشند.

خطای دانلود لینک‌های X (Twitter)

برخی لینک‌ها ممکن است به علت خصوصی یا تعلیق بودن حساب، محدودیت دسترسی، حساس بودن محتوا یا نبود ویدئو قابل دانلود نباشند. Python 3.11 نیز به‌تنهایی این محدودیت‌ها را برطرف نمی‌کند.

10. نکات امنیتی

- Bot Token و API Hash را در اختیار دیگران قرار ندهید.
- فایل ".env" را در GitHub آپلود نکنید.
- فایل‌های Session تلگرام و روبیکا را منتشر نکنید.
- از پوشه تنظیمات و نشست‌ها نسخه پشتیبان داشته باشید.

---

GitHub Repository:

https://github.com/mehrdaden/Tgtorub
