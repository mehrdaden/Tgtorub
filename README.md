Tgtorub

Telegram ↔ Rubika File Transfer Bot

ربات انتقال فایل و لینک بین تلگرام و روبیکا

---

1. امکانات

- انتقال فایل بین تلگرام و روبیکا
- دریافت و دانلود لینک‌های اینترنتی
- پشتیبانی از yt-dlp
- مدیریت دانلود و آپلود فایل
- اجرای دائمی ربات روی سرور
- راه‌اندازی خودکار پس از Restart سرور

---

2. پیش‌نیازها

- Ubuntu 22.04 یا Ubuntu 24.04
- دسترسی Root به سرور
- اتصال اینترنت
- حساب تلگرام
- حساب روبیکا

---

3. دریافت اطلاعات تلگرام

برای نصب ربات به سه اطلاعات اصلی تلگرام نیاز دارید.

3.1 ساخت Telegram Bot

1. وارد تلگرام شوید.
2. به ربات "@BotFather" (https://t.me/BotFather) مراجعه کنید.
3. دستور زیر را ارسال کنید:

"/newbot"

4. نام و username ربات را انتخاب کنید.
5. مقدار Bot Token را دریافت و ذخیره کنید.

نمونه Bot Token:

"123456789:ABCDEFxxxxxxxxxxxxxxxxxxxx"

3.2 دریافت API ID و API Hash

1. وارد سایت زیر شوید:

https://my.telegram.org

2. با شماره تلفن تلگرام وارد شوید.
3. گزینه API development tools را انتخاب کنید.
4. یک Application ایجاد کنید.
5. دو مقدار زیر را دریافت کنید:

- API ID
- API Hash

این اطلاعات محرمانه هستند و نباید در اختیار دیگران قرار بگیرند.

3.3 دریافت Telegram User ID

1. وارد تلگرام شوید.
2. به "@userinfobot" (https://t.me/userinfobot) مراجعه کنید.
3. شناسه عددی حساب خود را دریافت کنید.

---

4. نصب روی Ubuntu

با استفاده از SSH به سرور متصل شوید.

ابتدا وارد حساب Root شوید:

sudo -i

سپس دستور نصب را اجرا کنید:

bash <(curl -fsSL https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install-online.sh)

عملکرد نصب‌کننده

نصب‌کننده به‌صورت خودکار مراحل زیر را انجام می‌دهد:

1. نصب پیش‌نیازهای سیستم
2. نصب Python 3.11
3. دریافت پروژه از GitHub
4. ایجاد محیط مجازی Python
5. نصب کتابخانه‌های موردنیاز
6. ایجاد سرویس systemd
7. راه‌اندازی ربات

نکته مهم: نصب‌کننده فعلی به فایل تنظیمات ".env" موجود نیاز دارد. بنابراین این نسخه برای نصب یا به‌روزرسانی روی سروری است که تنظیمات اولیه آن آماده شده باشد.

---

5. فایل تنظیمات (.env)

مسیر فایل تنظیمات:

/opt/tg2rub/app/.env

نمونه ساختار تنظیمات:

BOT_TOKEN=YOUR_BOT_TOKEN
API_ID=YOUR_API_ID
API_HASH=YOUR_API_HASH
ADMIN_IDS=YOUR_TELEGRAM_USER_ID

مقادیر نمونه را با اطلاعات واقعی جایگزین کنید.

هشدار: فایل ".env" را در GitHub منتشر نکنید.

---

6. ورود به حساب روبیکا

در صورت نداشتن نشست قبلی روبیکا، دستور زیر را اجرا کنید:

sudo -u tg2rub /opt/tg2rub/app/venv/bin/python /opt/tg2rub/app/login_rubika.py

مراحل ورود را طبق پیام‌های برنامه دنبال کنید.

پس از ورود موفق، نشست روبیکا ذخیره می‌شود.

سپس سرویس را Restart کنید:

systemctl restart tg2rub

---

7. بررسی وضعیت ربات

برای بررسی وضعیت سرویس:

systemctl status tg2rub

برای مشاهده لاگ زنده:

journalctl -u tg2rub -f

برای مشاهده 100 خط آخر لاگ:

journalctl -u tg2rub -n 100 --no-pager

---

8. دستورات مدیریت ربات

شروع سرویس

systemctl start tg2rub

توقف سرویس

systemctl stop tg2rub

راه‌اندازی مجدد

systemctl restart tg2rub

فعال‌سازی اجرای خودکار هنگام روشن شدن سرور

systemctl enable tg2rub

غیرفعال کردن اجرای خودکار

systemctl disable tg2rub

---

9. به‌روزرسانی ربات

برای دریافت آخرین نسخه برنامه از شاخه "main" در GitHub، دستور نصب‌کننده را دوباره اجرا کنید:

bash <(curl -fsSL https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install-online.sh)

در فرایند به‌روزرسانی:

- فایل‌های برنامه از GitHub دریافت می‌شوند.
- وابستگی‌های Python نصب می‌شوند.
- محیط Python قبلی پشتیبان‌گیری می‌شود.
- فایل ".env" حفظ می‌شود.
- نشست‌های تلگرام و روبیکا عمداً حذف نمی‌شوند.
- سرویس مجدداً راه‌اندازی می‌شود.

قبل از به‌روزرسانی، از تنظیمات و فایل‌های Session نسخه پشتیبان تهیه کنید.

---

10. بررسی نسخه Python و yt-dlp

بررسی Python

/opt/tg2rub/app/venv/bin/python --version

نسخه مورد انتظار Python 3.11 یا بالاتر است.

بررسی yt-dlp

/opt/tg2rub/app/venv/bin/python -m yt_dlp --version

---

11. مشکلات متداول

خطای Python 3.10

نسخه‌های جدید yt-dlp ممکن است با Python 3.10 سازگار نباشند.

برای بررسی نسخه Python:

/opt/tg2rub/app/venv/bin/python --version

در صورت استفاده از نصب‌کننده فعلی، محیط جدید با Python 3.11 ساخته می‌شود.

خطای Telegram RPC

برخی خطاهای Telegram RPC موقتی هستند و ممکن است به اختلال سمت تلگرام مربوط باشند.

برای بررسی:

journalctl -u tg2rub -n 100 --no-pager

خطای دانلود از X (Twitter)

دلایل احتمالی:

- خصوصی بودن حساب یا پست
- تعلیق شدن حساب
- محدودیت دسترسی به محتوای حساس
- نبود ویدئو در پست
- محدودیت‌های احراز هویت

استفاده از Python 3.11 به‌تنهایی این محدودیت‌ها را برطرف نمی‌کند.

بررسی سرویس پس از خطا

systemctl restart tg2rub

سپس:

journalctl -u tg2rub -f

---

12. امنیت

- Bot Token را محرمانه نگه دارید.
- API Hash را منتشر نکنید.
- فایل ".env" را در GitHub قرار ندهید.
- فایل‌های Session تلگرام و روبیکا را منتشر نکنید.
- از اطلاعات ورود و تنظیمات سرور نسخه پشتیبان تهیه کنید.

---

Repository

GitHub:

https://github.com/mehrdaden/Tgtorub

Branch: "main"
