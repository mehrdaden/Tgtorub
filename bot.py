#!/usr/bin/env python3
"""tg2rub - Telegram bot  ->  Rubika (Saved Messages) uploader.

Telegram side : your own bot (token) over MTProto (Telethon): no local Bot API server,
                no compilation, up to 2 GB (4 GB with a Premium sender), parallel download.
Rubika side   : your account (rubpy), logged in once with phone number + code.
"""
from __future__ import annotations

import asyncio
import collections
import functools
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Tuple

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
os.chdir(BASE)                 # session files live next to the code
os.umask(0o077)                # everything we create is private
load_dotenv(BASE / ".env")


def _env(name: str, default: Optional[str] = None) -> str:
    v = os.getenv(name, default)
    if v is None or v == "":
        sys.exit("Missing %s in %s" % (name, BASE / ".env"))
    return v


# ------------------------------------------------------------------ config
BOT_TOKEN = _env("BOT_TOKEN")
API_ID = int(_env("API_ID"))
API_HASH = _env("API_HASH")
ADMIN_IDS = {int(x) for x in _env("ADMIN_IDS").replace(" ", "").split(",") if x}
WORK_DIR = Path(os.getenv("WORK_DIR", "/opt/tg2rub/work"))
MAX_FILE = int(float(os.getenv("MAX_FILE_GB", "4")) * 1024 ** 3)
RUBIKA_MAX = int(float(os.getenv("RUBIKA_MAX_MB", "1900")) * 1024 ** 2)   # auto-split above this
AUTO_DELETE = os.getenv("AUTO_DELETE", "1") == "1"
AUTO_UPLOAD = os.getenv("AUTO_UPLOAD", "0") == "1"
ZIP_LEVEL = int(os.getenv("ZIP_LEVEL", "0"))                 # 0 = store (fast) .. 9
UPLOAD_ATTEMPTS = max(1, min(int(os.getenv("UPLOAD_ATTEMPTS", "10")), 30))
UPLOAD_TIMEOUT = int(float(os.getenv("UPLOAD_TIMEOUT_MIN", "240")) * 60)
UPLOAD_MODE = os.getenv("UPLOAD_MODE", "auto")               # auto | bin | zip
DOWNLOAD_WORKERS = max(1, min(int(os.getenv("DOWNLOAD_WORKERS", "4")), 8))
PARALLEL_MIN = 16 * 1024 * 1024
UPLOAD_STREAMS = max(1, min(int(os.getenv("UPLOAD_STREAMS", "1")), 4))        # parts uploaded at the same time
MIN_UPLOAD_KBPS = float(os.getenv("MIN_UPLOAD_KBPS", "60"))                   # slower than this for a whole window = stalled
STALL_WINDOW = float(os.getenv("STALL_WINDOW_SEC", "90"))
STALL_GRACE = float(os.getenv("STALL_GRACE_SEC", "45"))
MAX_STALL_ABORTS = int(os.getenv("MAX_STALL_ABORTS", "3"))
LINK_PROXY = os.getenv("LINK_PROXY", "").strip() or None
COOKIES_FILE = os.getenv("COOKIES_FILE", "").strip() or None
LINK_CONNECTIONS = max(1, min(int(os.getenv("LINK_CONNECTIONS", "4")), 16))
YTDLP_MAX_HEIGHT = max(0, int(os.getenv("YTDLP_MAX_HEIGHT", "1080")))
RUBIKA_TARGET = os.getenv("RUBIKA_TARGET", "me").strip() or "me"
STALE_HOURS = int(os.getenv("STALE_HOURS", "24"))
SESSION_NAME = "rubika"
if UPLOAD_MODE not in ("auto", "bin", "zip") or not 0 <= ZIP_LEVEL <= 9 or MAX_FILE <= 0 or RUBIKA_MAX <= 0:
    sys.exit("Invalid configuration values in .env")


class Redact(logging.Filter):
    _tok = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")

    def filter(self, record):
        try:
            record.msg, record.args = self._tok.sub("<TOKEN>", record.getMessage()), ()
        except Exception:
            pass
        return True


logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
for _h in logging.getLogger().handlers:
    _h.addFilter(Redact())
for _n in ("telethon", "rubpy", "httpx", "httpcore", "asyncio"):
    logging.getLogger(_n).setLevel(logging.WARNING)
log = logging.getLogger("tg2rub")

# imports that need third-party packages come after config so errors are readable
from telethon import Button, TelegramClient, events  # noqa: E402
from telethon.errors import FloodWaitError, MessageNotModifiedError  # noqa: E402

from download import parallel_download  # noqa: E402
from link_download import LinkError, download_url, extract_urls, js_runtime_status  # noqa: E402
from nettest import general_upload, verdict  # noqa: E402
from fileops import safe_name  # noqa: E402
from pipeline import prepare_outputs  # noqa: E402
from uploader import RubikaSession, StallPolicy, UploadFailed, tx_bytes, upload_part  # noqa: E402

CLIENT: Optional[TelegramClient] = None
RUB = RubikaSession(SESSION_NAME, RUBIKA_TARGET)
UP_LOCK: Optional[asyncio.Lock] = None
DL_SEM: Optional[asyncio.Semaphore] = None
AWAIT: Dict[int, Tuple[str, str]] = {}                 # user_id -> (kind, job_id)
ERRORS: Deque[str] = collections.deque(maxlen=8)       # for /diag
MODES = ["auto", "bin", "zip"]
MODE_LABEL = {"auto": "خودکار", "bin": "بدون پسوند (.bin)", "zip": "داخل ZIP"}


# ------------------------------------------------------------------ model
@dataclass
class Job:
    id: str
    dir: Path
    name: str
    size: int
    chat_id: int
    user_id: int
    msg_id: int = 0
    zip: bool = False
    zip_password: Optional[str] = field(default=None, repr=False)   # memory only
    zip_password_required: bool = False
    parts: int = 1
    auto_delete: bool = AUTO_DELETE
    mode: str = UPLOAD_MODE
    sent: List[str] = field(default_factory=list)
    sig: Optional[str] = None
    state: str = "ready"            # downloading | ready | queued | running
    task: Optional["asyncio.Future"] = None
    progress: Tuple[int, int] = (0, 0)
    progress_approx: bool = False
    ts: float = field(default_factory=time.time)

    @property
    def src(self) -> Path:
        return self.dir / "src.bin"

    @property
    def busy(self) -> bool:
        return self.state != "ready"


JOBS: Dict[str, Job] = {}


# ------------------------------------------------------------------ helpers
def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, u)
        n /= 1024
    return "%.1f TB" % n


def fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%02d:%02d" % (m, s))


def bar(pct: float, width: int = 20) -> str:
    """Compact Telegram-friendly progress bar; percentage is shown to one decimal."""
    pct = max(0.0, min(100.0, pct))
    full = min(width, int(pct * width / 100.0))
    return "🟩" * full + "⬜" * (width - full) + "  %.1f%%" % pct


def progress_text(title: str, done: float, total: float, t0: float, approx: bool = False) -> str:
    el = max(time.monotonic() - t0, 0.1)
    prefix = "≈ " if approx else ""
    if total <= 0:
        return "%s\n━━━━━━━━━━━━━━━━━━━━\n⏱ زمان سپری‌شده: %s" % (title, fmt_time(el))
    pct = max(0.0, min(99.0 if approx else 100.0, done * 100.0 / total))
    speed = max(0.0, done) / el
    eta = (max(0.0, total - done) / speed) if speed > 0 else 0
    return ("%s\n━━━━━━━━━━━━━━━━━━━━\n%s%s\n"
            "📦 %s%s از %s\n⚡ سرعت: %s%s/s\n"
            "⏳ زمان باقی‌مانده: %s  |  ⏱ سپری‌شده: %s") % (
        title, prefix, bar(pct), prefix, human(min(max(done, 0.0), total)), human(total),
        prefix, human(speed), fmt_time(eta), fmt_time(el))


def short_error(exc: BaseException) -> str:
    s = "%s: %s" % (type(exc).__name__, str(exc))
    s = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "<TOKEN>", s)
    return re.sub(r"\s+", " ", s).strip()[:300]


def user_error(exc: BaseException) -> str:
    """Translate noisy downloader/uploader exceptions into concise actionable Persian."""
    raw = short_error(exc)
    low = raw.lower()
    if type(exc).__name__ == "StallError" or "upload too slow" in low:
        return "سرعت ارسال پایین ماند؛ اتصال دوباره امتحان شد. اگر تکرار شود، مسیر سرور تا روبیکا کند است."
    if isinstance(exc, UploadFailed):
        return "روبیکا پس از چند تلاش فایل را نپذیرفت. حالت ارسال را روی BIN یا ZIP بگذار و دوباره امتحان کن."
    if "sign in to confirm" in low or "not a bot" in low or "login required" in low:
        return "سایت برای دانلود ورود یا تأیید انسانی می‌خواهد. کوکی تازه و معتبر همان سایت را در COOKIES_FILE تنظیم کن."
    if "age-restricted" in low or "confirm your age" in low or "age restricted" in low:
        return "محتوا محدودیت سنی دارد؛ دانلود فقط با دسترسی مجاز و کوکی معتبر حساب خودت ممکن است."
    if "private" in low or "members-only" in low:
        return "این محتوا خصوصی یا مخصوص اعضاست و با دسترسی فعلی قابل دریافت نیست."
    if "unsupported url" in low:
        return "این سایت یا قالب لینک توسط نسخه فعلی yt-dlp پشتیبانی نمی‌شود."
    if "po token" in low or "sabr" in low:
        return "یوتیوب برای این فرمت PO Token/SABR می‌خواهد؛ تلاش جایگزین هم ناموفق بوده است. yt-dlp را به‌روز کن و محدودیت IP/کوکی را بررسی کن."
    if "403" in low or "forbidden" in low:
        return "سایت درخواست سرور را مسدود کرده (403). کوکی معتبر یا مسیر شبکه مجاز دیگری لازم است."
    if "429" in low or "too many requests" in low:
        return "سایت تعداد درخواست‌ها را محدود کرده (429). کمی صبر کن و دوباره امتحان کن."
    if "javascript runtime" in low or "js runtime" in low or "challenge solving failed" in low or "ejs" in low:
        return "حل چالش جاوااسکریپت ناموفق بود؛ Deno و بسته yt-dlp-ejs را در /diag بررسی کن."
    if "requested format is not available" in low or "no video formats" in low:
        return "فرمت قابل دریافت پیدا نشد؛ YTDLP_MAX_HEIGHT را کمتر کن یا کوکی معتبر تنظیم کن."
    if "404" in low or "not found" in low:
        return "لینک حذف شده، اشتباه است یا دیگر در دسترس نیست."
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "مهلت ارتباط تمام شد؛ اتصال سرور یا سایت مقصد را بررسی کن و دوباره تلاش کن."
    if isinstance(exc, ConnectionError):
        return "ارتباط شبکه قطع شد؛ اتصال سرور و پروکسی را بررسی کن."
    return raw[:220]


def run_blocking(fn: Callable, *a, **kw):
    return asyncio.get_running_loop().run_in_executor(None, functools.partial(fn, *a, **kw))


def admin(fn):
    @functools.wraps(fn)
    async def wrapper(event):
        if event.sender_id not in ADMIN_IDS:
            return                                  # silently ignore strangers
        try:
            await fn(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("handler %s failed", fn.__name__)
    return wrapper


def card(j: Job) -> str:
    out = j.name + (".zip" if j.zip else "")
    if j.parts > 1:
        out += "  →  %d قسمت (.001 …)" % j.parts
    pw = "فعال (AES-256)" if j.zip_password else ("نیازمند ورود مجدد رمز" if j.zip_password_required else "بدون رمز")
    return ("📄 %s\n📦 حجم: %s\n🗜 ZIP: %s\n🔐 رمز ZIP: %s\n✂️ تقسیم: %s\n"
            "🛡 حالت آپلود: %s\n🧹 حذف خودکار بعد آپلود: %s\n\nخروجی: %s" % (
                j.name, human(j.size), "روشن" if j.zip else "خاموش", pw,
                j.parts if j.parts > 1 else "خاموش", MODE_LABEL[j.mode],
                "بله" if j.auto_delete else "خیر", out))


def keyboard(j: Job):
    i = j.id
    B = Button.inline
    return [
        [B("✏️ تغییر نام", ("rn:" + i).encode()), B("🗜 ZIP: " + ("روشن" if j.zip else "خاموش"), ("zp:" + i).encode())],
        [B("🔐 رمز ZIP", ("pw:" + i).encode()), B("🔓 حذف رمز", ("p0:" + i).encode())],
        [B("✂️ تقسیم: " + (str(j.parts) if j.parts > 1 else "خاموش"), ("sp:" + i).encode()),
         B("🧹 حذف بعد آپلود: " + ("بله" if j.auto_delete else "خیر"), ("ad:" + i).encode())],
        [B("🛡 حالت آپلود: " + MODE_LABEL[j.mode], ("md:" + i).encode())],
        [B("🚀 آپلود به روبیکا", ("up:" + i).encode())],
        [B("🗑 حذف از سرور", ("rm:" + i).encode())],
    ]


def cancel_kb(j: Job):
    return [[Button.inline("⛔ لغو", ("cx:" + j.id).encode())]]


async def show(j: Job, text: str, buttons=None) -> None:
    try:
        await CLIENT.edit_message(j.chat_id, j.msg_id, text, buttons=buttons)
    except MessageNotModifiedError:
        pass
    except FloodWaitError as e:
        log.warning("edit flood wait %ss", getattr(e, "seconds", "?"))
    except Exception as e:
        log.warning("edit failed: %s", type(e).__name__)


async def ticker(j: Job, render: Callable[[], str], interval: float = 4.0) -> None:
    while True:
        await asyncio.sleep(interval)
        await show(j, render(), cancel_kb(j))


def persist(j: Job) -> None:
    j.dir.mkdir(parents=True, exist_ok=True)
    data = {"id": j.id, "name": j.name, "size": j.size, "chat_id": j.chat_id, "msg_id": j.msg_id,
            "user_id": j.user_id, "zip": j.zip, "zip_password_required": j.zip_password_required or bool(j.zip_password),
            "parts": j.parts, "auto_delete": j.auto_delete, "mode": j.mode, "sent": j.sent, "sig": j.sig, "ts": j.ts}
    fd, tmp = tempfile.mkstemp(prefix=".job-", dir=str(j.dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, j.dir / "job.json")
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def restore_jobs() -> None:
    for child in list(WORK_DIR.iterdir()):
        if not child.is_dir():
            child.unlink()
            continue
        try:
            d = json.loads((child / "job.json").read_text(encoding="utf-8"))
            jid = str(d["id"])
            if not re.fullmatch(r"[0-9a-f]{8}", jid) or child.name != jid:
                raise ValueError("bad id")
            src = child / "src.bin"
            if not src.is_file() or src.stat().st_size <= 0:
                raise ValueError("no source")
            JOBS[jid] = Job(
                id=jid, dir=child, name=safe_name(str(d.get("name", "file"))), size=src.stat().st_size,
                chat_id=int(d["chat_id"]), user_id=int(d.get("user_id", 0)), msg_id=int(d.get("msg_id", 0)),
                zip=bool(d.get("zip")), zip_password_required=bool(d.get("zip_password_required")),
                parts=max(1, min(int(d.get("parts", 1)), 50)), auto_delete=bool(d.get("auto_delete", AUTO_DELETE)),
                mode=d.get("mode") if d.get("mode") in MODES else "auto",
                sent=[str(x) for x in d.get("sent", [])], sig=d.get("sig"), ts=float(d.get("ts", time.time())))
        except Exception:
            shutil.rmtree(child, ignore_errors=True)      # incomplete download / crash leftovers
    if JOBS:
        log.info("restored %d pending job(s)", len(JOBS))


def drop(j: Job) -> None:
    JOBS.pop(j.id, None)
    shutil.rmtree(j.dir, ignore_errors=True)


def reset_progress_marks(j: Job) -> None:
    j.sent.clear()
    j.sig = None


# ------------------------------------------------------------------ receive (download)
EXT_BY_KIND = {"video": ".mp4", "audio": ".mp3", "voice": ".ogg", "photo": ".jpg", "gif": ".mp4"}


async def download_job(j: Job, msg) -> None:
    t0 = time.monotonic()
    dl = j.dir / "dl.part"
    tick = asyncio.ensure_future(ticker(j, lambda: progress_text("⏬ دریافت از تلگرام\n" + j.name, j.progress[0], j.progress[1] or j.size, t0)))

    def cb(cur: int, total: int) -> None:
        j.progress = (cur, total or j.size)

    try:
        async with DL_SEM:
            if j.size >= PARALLEL_MIN and DOWNLOAD_WORKERS > 1:
                try:
                    await parallel_download(CLIENT, msg, dl, j.size, workers=DOWNLOAD_WORKERS, progress=cb)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("parallel download failed (%s); falling back to sequential", short_error(e))
                    if dl.exists():
                        dl.unlink()
                    j.progress = (0, j.size)
                    await CLIENT.download_media(msg, file=str(dl), progress_callback=cb)
            else:
                await CLIENT.download_media(msg, file=str(dl), progress_callback=cb)
        if not dl.exists():                                   # Telethon may add an extension
            alt = sorted(j.dir.glob("dl*"))
            if alt:
                alt[0].rename(dl)
        actual = dl.stat().st_size
        if actual <= 0:
            raise IOError("received file is empty")
        if j.size and actual != j.size:
            raise IOError("size mismatch: expected %d, got %d" % (j.size, actual))
        if actual > MAX_FILE:
            raise IOError("file exceeds MAX_FILE_GB")
        os.replace(dl, j.src)
        j.size = actual
        j.state = "ready"
        j.ts = time.time()
        persist(j)
    except asyncio.CancelledError:
        drop(j)
        await show(j, "⛔ دریافت لغو شد و فایل از سرور پاک شد.")
        return
    except Exception as e:
        ERRORS.append("download: " + short_error(e))
        log.exception("download failed")
        drop(j)
        await show(j, "❌ دریافت فایل ناموفق بود\n━━━━━━━━━━━━━━━━━━━━\n" + user_error(e))
        return
    finally:
        tick.cancel()
    await show(j, card(j), keyboard(j))
    if AUTO_UPLOAD:
        start_upload(j)


async def download_link_job(j: Job, url: str) -> None:
    t0 = time.monotonic()
    host = (urlsplit(url).hostname or "link")
    tick = asyncio.ensure_future(ticker(
        j, lambda: progress_text("⏬ دانلود از %s" % host, j.progress[0], j.progress[1], t0, approx=j.progress_approx)))

    def cb(cur: int, total: int) -> None:
        j.progress = (cur, abs(total))
        j.progress_approx = total < 0

    try:
        async with DL_SEM:
            downloaded = await download_url(url, j.dir / "link", MAX_FILE, progress=cb, proxy=LINK_PROXY,
                                            cookies=COOKIES_FILE, connections=LINK_CONNECTIONS,
                                            max_height=YTDLP_MAX_HEIGHT)
        actual = downloaded.stat().st_size
        if actual <= 0 or actual > MAX_FILE:
            raise IOError("downloaded file is empty or exceeds MAX_FILE_GB")
        name = safe_name(downloaded.name)
        if not name or name in (".", ".."):
            name = "download.bin"
        j.name = name
        os.replace(downloaded, j.src)
        j.size = actual
        j.state = "ready"
        j.ts = time.time()
        persist(j)
    except asyncio.CancelledError:
        drop(j)
        await show(j, "⛔ دانلود لغو شد و فایل موقت پاک شد.")
        return
    except Exception as e:
        ERRORS.append("link download: " + short_error(e))
        log.warning("link download failed: %s", short_error(e))
        drop(j)
        msg = user_error(e)
        await show(j, "❌ دانلود ناموفق بود\n━━━━━━━━━━━━━━━━━━━━\n" + msg)
        return
    finally:
        tick.cancel()
        shutil.rmtree(j.dir / "link", ignore_errors=True)
    await show(j, "✅ دانلود کامل شد.\n\n" + card(j), keyboard(j))
    if AUTO_UPLOAD:
        start_upload(j)


async def start_link_job(event, url: str) -> None:
    if shutil.disk_usage(WORK_DIR).free < (256 << 20):
        await event.reply("❌ فضای دیسک سرور کافی نیست. /clean بزن.")
        return
    jid = secrets.token_hex(4)
    j = Job(id=jid, dir=WORK_DIR / jid, name="link", size=0, chat_id=event.chat_id,
            user_id=event.sender_id, state="downloading")
    j.dir.mkdir(parents=True)
    status = await event.reply("🔗 %s\nدر حال بررسی لینک…" % (urlsplit(url).hostname or url), buttons=cancel_kb(j))
    j.msg_id = status.id
    JOBS[jid] = j
    j.task = asyncio.ensure_future(download_link_job(j, url))


@admin
async def on_media(event) -> None:
    msg = event.message
    f = msg.file
    kind = "photo" if msg.photo else ("video" if msg.video else ("voice" if msg.voice else ("audio" if msg.audio else "file")))
    size = int(getattr(f, "size", 0) or 0)
    if size > MAX_FILE:
        await event.reply("❌ حجم فایل بیشتر از %s است." % human(MAX_FILE))
        return
    if shutil.disk_usage(WORK_DIR).free < (size or MAX_FILE) * 1.1 + (256 << 20):
        await event.reply("❌ فضای دیسک سرور کافی نیست. /clean بزن.")
        return
    name = safe_name(getattr(f, "name", None) or "%s_%d%s" % (kind, msg.id, getattr(f, "ext", None) or EXT_BY_KIND.get(kind, "")))
    jid = secrets.token_hex(4)
    j = Job(id=jid, dir=WORK_DIR / jid, name=name, size=size, chat_id=event.chat_id, user_id=event.sender_id, state="downloading")
    j.dir.mkdir(parents=True)
    status = await event.reply("⏬ در صف دریافت…\n%s (%s)" % (name, human(size)), buttons=cancel_kb(j))
    j.msg_id = status.id
    JOBS[jid] = j
    j.task = asyncio.ensure_future(download_job(j, msg))


# ------------------------------------------------------------------ upload job
def signature(j: Job) -> str:
    pw = "pw" if (j.zip_password or j.zip_password_required) else "nopw"
    return "|".join(map(str, (j.name, int(j.zip), j.parts, ZIP_LEVEL, RUBIKA_MAX, pw, j.size)))


def start_upload(j: Job) -> None:
    if j.busy:
        return
    j.state = "queued"
    j.task = asyncio.ensure_future(run_job(j))


async def run_job(j: Job) -> None:
    out = j.dir / "out"
    t0 = time.monotonic()
    tick = None
    tasks: List["asyncio.Future"] = []
    try:
        if UP_LOCK.locked():
            await show(j, "⏳ در صف آپلود…\n" + j.name, cancel_kb(j))
        async with UP_LOCK:
            j.state = "running"
            if j.zip and j.zip_password_required and not j.zip_password:
                raise RuntimeError("رمز ZIP پس از ری‌استارت در حافظه نیست؛ دوباره «🔐 رمز ZIP» را بزن.")
            sg = signature(j)
            if j.sig != sg:
                j.sent.clear()
                j.sig = sg
                persist(j)
            splitting = j.parts > 1 or j.size > RUBIKA_MAX
            need = j.size * (int(j.zip) + int(splitting)) + (128 << 20)
            if shutil.disk_usage(WORK_DIR).free < need:
                raise RuntimeError("فضای دیسک کافی نیست (حدود %s لازم است)" % human(need))

            shutil.rmtree(out, ignore_errors=True)
            await show(j, "⚙️ آماده‌سازی (ZIP / تغییر نام / تقسیم)…\n" + j.name, cancel_kb(j))
            files = await run_blocking(prepare_outputs, j.src, j.name, out, zip_enabled=j.zip, password=j.zip_password,
                                       parts=j.parts, max_part=RUBIKA_MAX, zip_level=ZIP_LEVEL)
            total_parts = len(files)
            todo: List[Path] = []
            for p in files:
                if p.name in j.sent:
                    p.unlink()
                else:
                    todo.append(p)
            streams = max(1, min(UPLOAD_STREAMS, len(todo)))
            states: Dict[str, dict] = {
                p.name: {"size": p.stat().st_size, "attempt": 1, "of": UPLOAD_ATTEMPTS, "kind": "orig", "sent": 0,
                         "t0": time.monotonic(), "done": False, "err": ""} for p in todo}
            agg = {"tx0": tx_bytes(), "t0": time.monotonic(), "total": sum(s["size"] for s in states.values())}
            notes: List[str] = []
            sem = asyncio.Semaphore(streams)

            def render() -> str:
                left = [(n, s) for n, s in states.items() if not s["done"]]
                head = "📤 آپلود به روبیکا — %d/%d قسمت" % (total_parts - len(left), total_parts)
                if not left:
                    return head
                have_tx = tx_bytes() is not None
                if streams == 1:
                    n, s = left[0]
                    text = "%s\n%s (%s)\n🔁 تلاش %d/%d (%s)" % (head, n, human(s["size"]), s["attempt"], s["of"], s["kind"])
                    text = progress_text(text, s["sent"], s["size"], s["t0"], approx=True) if have_tx else \
                        text + "\n⏱ " + fmt_time(time.monotonic() - s["t0"])
                else:
                    lines = ["• %s (%s) 🔁 %d/%d" % (n, human(s["size"]), s["attempt"], s["of"]) for n, s in left[:streams]]
                    now = tx_bytes()
                    sent = max(0, now - agg["tx0"]) if (now is not None and agg["tx0"] is not None) else 0
                    text = head + "\n" + "\n".join(lines)
                    text = progress_text(text, min(sent, agg["total"]), agg["total"], agg["t0"], approx=True) if have_tx else \
                        text + "\n⏱ " + fmt_time(time.monotonic() - agg["t0"])
                errs = [s["err"] for _, s in left if s["err"]]
                return text + (("\n⚠️ " + errs[-1]) if errs else "")

            async def one(p: Path) -> None:
                st = states[p.name]
                pol = StallPolicy(min_speed=MIN_UPLOAD_KBPS * 1024, window=STALL_WINDOW, grace=STALL_GRACE,
                                  max_aborts=MAX_STALL_ABORTS, enabled=MIN_UPLOAD_KBPS > 0)

                async def on_attempt(i, of, kind):
                    st.update(attempt=i, of=of, kind=kind, sent=0, t0=time.monotonic())
                    await show(j, render(), cancel_kb(j))

                def on_progress(sent, el):
                    st["sent"] = sent

                def on_error(i, kind, exc):
                    ERRORS.append("upload[%s #%d]: %s" % (kind, i, short_error(exc)))
                    st["err"] = user_error(exc)[:140]

                async with sem:
                    res = await upload_part(RUB, p, attempts=UPLOAD_ATTEMPTS, mode=j.mode, timeout=UPLOAD_TIMEOUT,
                                            policy=pol, monitor=(streams == 1), on_attempt=on_attempt,
                                            on_progress=on_progress, on_error=on_error)
                st["done"] = True
                j.sent.append(p.name)
                persist(j)
                p.unlink()
                notes.append("• %s: میانگین ≈ %s/s" % (p.name, human(st["size"] / max(res.seconds, 0.1))))
                if res.kind != "orig":
                    notes.append("  روش جایگزین: %s (%s)" % (
                        {"ascii": "نام ASCII", "bin": ".bin", "zip": "داخل ZIP"}.get(res.kind, res.kind), res.name))

            if todo:
                tick = asyncio.ensure_future(ticker(j, render))
                tasks = [asyncio.ensure_future(one(p)) for p in todo]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                for t in done:
                    t.result()                      # re-raises the first failure
                tick.cancel()

            shutil.rmtree(out, ignore_errors=True)
            elapsed = fmt_time(time.monotonic() - t0)
            extra = ("\n\n" + "\n".join(notes)) if notes else ""
            if j.auto_delete:
                name = j.name
                drop(j)
                await show(j, "✅ آپلود شد و از سرور حذف شد.\n%s\n⏱ %s%s" % (name, elapsed, extra))
            else:
                j.sent.clear()
                j.state = "ready"
                persist(j)
                await show(j, "✅ آپلود شد (فایل هنوز روی سرور است) ⏱ %s%s\n\n%s" % (elapsed, extra, card(j)), keyboard(j))
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        shutil.rmtree(out, ignore_errors=True)
        if j.id in JOBS:
            j.state = "ready"
            persist(j)
            await show(j, "⛔ لغو شد (فایل هنوز روی سرور است).\n\n" + card(j), keyboard(j))
    except Exception as e:
        reason = short_error(e.last) if isinstance(e, UploadFailed) else short_error(e)
        ERRORS.append("job %s: %s" % (j.id, reason))
        log.error("job %s failed: %s", j.id, reason)
        shutil.rmtree(out, ignore_errors=True)
        if j.id in JOBS:
            j.state = "ready"
            persist(j)
            hint = "\n\n💡 حالت آپلود را روی «بدون پسوند (.bin)» یا «داخل ZIP» بگذار و دوباره بزن." if isinstance(e, UploadFailed) else ""
            await show(j, "❌ عملیات ناموفق بود\n━━━━━━━━━━━━━━━━━━━━\n%s%s\n\n%s" % (user_error(e), hint, card(j)), keyboard(j))
    finally:
        if tick is not None:
            tick.cancel()
        if j.id in JOBS:
            j.ts = time.time()
            if j.state != "ready":
                j.state = "ready"


# ------------------------------------------------------------------ buttons
@admin
async def on_button(event) -> None:
    act, _, jid = event.data.decode("utf-8", "ignore").partition(":")
    j = JOBS.get(jid)
    if not j:
        await event.answer("این فایل دیگر روی سرور نیست.", alert=True)
        return
    if act == "cx":
        if j.task and not j.task.done():
            j.task.cancel()
            await event.answer("در حال لغو…")
        else:
            await event.answer()
        return
    if j.busy:
        await event.answer("در حال پردازش… برای توقف «⛔ لغو» را بزن.")
        return
    await event.answer()
    j.ts = time.time()
    uid = event.sender_id

    if act == "op":
        m = await CLIENT.send_message(event.chat_id, card(j), buttons=keyboard(j))
        j.chat_id, j.msg_id = m.chat_id, m.id
        persist(j)
        return
    if act == "zp":
        j.zip = not j.zip
        reset_progress_marks(j)
    elif act == "ad":
        j.auto_delete = not j.auto_delete
    elif act == "md":
        j.mode = MODES[(MODES.index(j.mode) + 1) % len(MODES)]
    elif act == "p0":
        j.zip_password, j.zip_password_required = None, False
        reset_progress_marks(j)
    elif act == "pw":
        AWAIT[uid] = ("pw", jid)
        await CLIENT.send_message(event.chat_id, "🔐 رمز ZIP را بفرست (۸ تا ۱۲۸ نویسه). پیام رمز بلافاصله پاک می‌شود. /cancel برای لغو")
        return
    elif act == "rn":
        AWAIT[uid] = ("rn", jid)
        await CLIENT.send_message(event.chat_id, "✏️ نام جدید را بفرست (اگر پسوند ننویسی، پسوند قبلی حفظ می‌شود). /cancel برای لغو")
        return
    elif act == "sp":
        AWAIT[uid] = ("sp", jid)
        await CLIENT.send_message(event.chat_id, "✂️ به چند قسمت تقسیم شود؟ عدد ۱ تا ۵۰ (۱ = بدون تقسیم). /cancel برای لغو")
        return
    elif act == "up":
        persist(j)
        j.chat_id, j.msg_id = event.chat_id, event.message_id if hasattr(event, "message_id") else j.msg_id
        start_upload(j)
        return
    elif act == "rm":
        drop(j)
        await show(j, "🗑 از سرور حذف شد.")
        return
    persist(j)
    await show(j, card(j), keyboard(j))


@admin
async def on_text(event) -> None:
    st = AWAIT.pop(event.sender_id, None)
    if not st:
        for url in extract_urls(event.raw_text or "", 5):
            await start_link_job(event, url)
        return
    kind, jid = st
    j = JOBS.get(jid)
    raw = event.raw_text or ""
    if kind == "pw":
        try:
            await event.delete()                 # remove the password message first
        except Exception:
            log.warning("could not delete the password message")
    if not j or j.busy:
        await event.reply("این فایل دیگر در دسترس نیست.")
        return
    t = raw if kind == "pw" else raw.strip()
    if kind == "rn":
        new = safe_name(t, j.name)
        if not Path(new).suffix and Path(j.name).suffix:
            new += Path(j.name).suffix
        if new != j.name:
            j.name = new
            reset_progress_marks(j)
    elif kind == "pw":
        if not 8 <= len(t) <= 128:
            AWAIT[event.sender_id] = st
            await event.reply("رمز باید ۸ تا ۱۲۸ نویسه باشد؛ دوباره بفرست یا /cancel بزن.")
            return
        j.zip_password, j.zip_password_required, j.zip = t, True, True
        reset_progress_marks(j)
    elif kind == "sp":
        if not t.isdigit() or not 1 <= int(t) <= 50:
            AWAIT[event.sender_id] = st
            await event.reply("یک عدد بین ۱ تا ۵۰ بفرست.")
            return
        if int(t) != j.parts:
            j.parts = int(t)
            reset_progress_marks(j)
    persist(j)
    await show(j, card(j), keyboard(j))
    if kind != "pw":
        try:
            await event.delete()
        except Exception:
            pass


# ------------------------------------------------------------------ commands
HELP = ("سلام 👋\nفایل بفرست، یا لینک بفرست (فایل مستقیم، یوتیوب، اینستاگرام، توییتر/X، تیک‌تاک و سایت‌های دیگر در صورت پشتیبانی)؛ "
        "بعد با دکمه‌ها نام، ZIP، رمز و تقسیم را تنظیم کن و «🚀 آپلود به روبیکا» را بزن. "
        "درصد دانلود دقیق و درصد آپلود تخمینی نمایش داده می‌شود؛ Rubpy فعلاً callback قابل‌اتکای بایت آپلود ارائه نمی‌کند.\n\n"
        "/list فایل‌های روی سرور\n/disk فضای دیسک\n/clean حذف همه فایل‌ها از سرور\n"
        "/test تست اتصال روبیکا\n/speed [full] تست سرعت آپلود\n/ytdlp_update به‌روزرسانی دانلودر لینک\n"
        "/diag وضعیت و آخرین خطاها\n/cancel لغو ورودی در حال انتظار")


@admin
async def on_command(event) -> None:
    cmd = (event.raw_text or "").split()[0][1:].split("@")[0].lower()
    if cmd in ("start", "help"):
        await event.reply(HELP)
    elif cmd == "list":
        if not JOBS:
            await event.reply("هیچ فایلی روی سرور نیست.")
            return
        rows = [[Button.inline(("%s (%s)" % (j.name, human(j.size)))[:60], ("op:" + j.id).encode())] for j in JOBS.values()]
        await event.reply("فایل‌های روی سرور:", buttons=rows)
    elif cmd == "disk":
        d = shutil.disk_usage(WORK_DIR)
        await event.reply("💾 آزاد: %s از %s\nفایل‌های ربات: %d (%s)" % (
            human(d.free), human(d.total), len(JOBS), human(sum(j.size for j in JOBS.values()))))
    elif cmd == "clean":
        n = 0
        for j in list(JOBS.values()):
            if not j.busy:
                drop(j)
                n += 1
        await event.reply("🗑 %d فایل حذف شد." % n + (" (موارد در حال پردازش دست‌نخورده ماند)" if any(j.busy for j in JOBS.values()) else ""))
    elif cmd == "test":
        try:
            await asyncio.wait_for(RUB.send_text("✅ tg2rub test"), 45)
            await event.reply("✅ اتصال روبیکا سالم است (پیام در Saved Messages).")
        except Exception as e:
            ERRORS.append("test: " + short_error(e))
            await event.reply("❌ اتصال روبیکا برقرار نشد\n━━━━━━━━━━━━━━━━━━━━\n" + user_error(e))
    elif cmd == "diag":
        from importlib import metadata

        def ver(p):
            try:
                return metadata.version(p)
            except Exception:
                return "?"
        d = shutil.disk_usage(WORK_DIR)
        await event.reply("🩺 وضعیت tg2rub v6.1\n━━━━━━━━━━━━━━━━━━━━\nPython %s\nTelethon %s | Rubpy %s\nyt-dlp %s | yt-dlp-ejs %s\n💾 فضای آزاد: %s\n📁 فایل‌های فعال: %d\n🔁 تلاش آپلود: %d | حالت: %s\n⬇️ دانلود موازی: %d | ⬆️ آپلود هم‌زمان: %d\n🐢 حد توقف سرعت: %.0f KB/s\n🟢 JavaScript: %s\n━━━━━━━━━━━━━━━━━━━━\nآخرین خطاها:\n%s" % (
                              sys.version.split()[0], ver("telethon"), ver("rubpy"), ver("yt-dlp"), ver("yt-dlp-ejs"), human(d.free), len(JOBS),
                              UPLOAD_ATTEMPTS, UPLOAD_MODE, DOWNLOAD_WORKERS, UPLOAD_STREAMS, MIN_UPLOAD_KBPS,
                              js_runtime_status(),
                              "\n".join(ERRORS) or "—"))
    elif cmd == "speed":
        await speed_test(event, full=("full" in (event.raw_text or "").lower()))
    elif cmd == "ytdlp_update":
        await ytdlp_update(event)
    elif cmd == "cancel":
        AWAIT.pop(event.sender_id, None)
        await event.reply("لغو شد.")


# ------------------------------------------------------------------ lifecycle
SPEED_BUSY = {"on": False}


async def speed_test(event, full: bool = False) -> None:
    """/speed [full]  - tells whether the bottleneck is the server, the route to Rubika, or per-connection throttling."""
    if SPEED_BUSY["on"]:
        await event.reply("یک تست سرعت در حال اجراست.")
        return
    if UP_LOCK.locked():
        await event.reply("⚠️ یک آپلود در حال انجام است؛ نتیجه‌ی تست دقیق نخواهد بود. بعد از پایان آپلود دوباره بزن.")
        return
    SPEED_BUSY["on"] = True
    status = await event.reply("🧪 سنجش سرعت ارتباط\n━━━━━━━━━━━━━━━━━━━━\n⏳ در حال آماده‌سازی تست‌ها…")
    lines: List[str] = []
    general = single = parallel = None
    tdir = WORK_DIR / ("speed_" + secrets.token_hex(3))
    tdir.mkdir(parents=True, exist_ok=True)

    async def upd(extra: str = "") -> None:
        try:
            await CLIENT.edit_message(status.chat_id, status.id, "🧪 گزارش سرعت\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) + extra)
        except Exception:
            pass

    async def rub_upload(n: int, size: int) -> float:
        paths = []
        for i in range(n):
            p = tdir / ("speedtest_%d_%d.bin" % (size // 1024, i))
            with open(p, "wb") as f:
                f.write(os.urandom(size))
            paths.append(p)
        t = time.monotonic()
        await asyncio.wait_for(asyncio.gather(*[RUB.send_file(p, "tg2rub speedtest") for p in paths]), 300)
        return (size * n) / max(time.monotonic() - t, 0.001)

    try:
        await upd("\n⏳ سرعت عمومی سرور…")
        try:
            general, _ = await general_upload(8 * 1024 * 1024, LINK_PROXY)
            lines.append("🌐 آپلود عمومی سرور: %s/s" % human(general))
        except Exception as e:
            lines.append("🌐 تست عمومی ناموفق: %s" % user_error(e))
        await upd("\n⏳ آپلود ۴MB به روبیکا…")
        try:
            single = await rub_upload(1, 4 * 1024 * 1024)
            lines.append("📤 روبیکا (۱ اتصال): %s/s" % human(single))
        except Exception as e:
            lines.append("📤 تست روبیکا ناموفق: %s" % user_error(e))
        if full and single:
            await upd("\n⏳ آپلود موازی (۳ اتصال)…")
            try:
                parallel = await rub_upload(3, 4 * 1024 * 1024)
                lines.append("📤 روبیکا (۳ اتصال هم‌زمان، مجموع): %s/s" % human(parallel))
            except Exception as e:
                lines.append("📤 تست موازی ناموفق: %s" % user_error(e))
        lines.append("\n" + verdict(general, single, parallel))
        if single:
            lines.append("(فایل‌های speedtest_*.bin داخل Saved Messages می‌مانند؛ دستی پاکشان کن.)")
        await upd()
    finally:
        SPEED_BUSY["on"] = False
        shutil.rmtree(tdir, ignore_errors=True)


async def ytdlp_update(event) -> None:
    """/ytdlp_update - sites change often; this upgrades yt-dlp inside the bot's virtualenv."""
    from importlib import metadata

    def ver() -> str:
        try:
            return metadata.version("yt-dlp")
        except Exception:
            return "?"
    before = ver()
    status = await event.reply("⏳ در حال به‌روزرسانی yt-dlp و مؤلفه YouTube…")
    # Keep the live updater inside the same tested major-version range as requirements.txt.
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", "-U", "--disable-pip-version-check",
        "yt-dlp[default]>=2026.10.26,<2027",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 300)
    except asyncio.TimeoutError:
        proc.kill()
        await status.edit("❌ زمان به‌روزرسانی تمام شد.")
        return
    if proc.returncode == 0:
        await status.edit("✅ به‌روزرسانی کامل شد\nyt-dlp: %s ← %s\nyt-dlp-ejs: %s" % (before, ver(), __import__("importlib.metadata", fromlist=["version"]).version("yt-dlp-ejs")))
    else:
        detail = out.decode("utf-8", "replace").splitlines()
        detail = "\n".join(x.strip() for x in detail[-3:] if x.strip())
        await status.edit("❌ به‌روزرسانی انجام نشد\n━━━━━━━━━━━━━━━━━━━━\nبسته‌ها از مخزن دریافت نشدند. اتصال اینترنت و دسترسی PyPI را بررسی کن.\n" + detail[-220:])


async def janitor() -> None:
    while True:
        await asyncio.sleep(1800)
        now = time.time()
        for j in list(JOBS.values()):
            if not j.busy and now - j.ts > STALE_HOURS * 3600:
                log.info("dropping stale job %s", j.id)
                drop(j)


async def watchdog() -> None:
    """If Telegram stops answering for ~6 minutes, exit so systemd restarts us cleanly."""
    bad = 0
    while True:
        await asyncio.sleep(120)
        try:
            await asyncio.wait_for(CLIENT.get_me(), 60)
            bad = 0
        except Exception as e:
            bad += 1
            log.warning("watchdog: Telegram check failed (%d/3): %s", bad, type(e).__name__)
            if bad >= 3:
                log.error("watchdog: restarting process")
                os._exit(1)


FATAL_LOGIN = {"AccessTokenInvalidError", "AccessTokenExpiredError", "ApiIdInvalidError", "ApiIdPublishedFloodError"}


async def tg_login(client, sleep=asyncio.sleep) -> None:
    """Sign in as the bot. Retries with growing delays instead of crash-looping (which can cause flood bans)."""
    delay, attempt = 10, 0
    while True:
        attempt += 1
        try:
            await client.start(bot_token=BOT_TOKEN)
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            name = type(e).__name__
            if name in FATAL_LOGIN:
                sys.exit("Telegram rejected the credentials (%s). Check BOT_TOKEN / API_ID / API_HASH in %s" % (name, BASE / ".env"))
            wait = delay
            if name == "FloodWaitError":
                wait = int(getattr(e, "seconds", 60)) + 5
            log.error("Telegram bot login failed (attempt %d): %s", attempt, short_error(e))
            if name == "RpcCallFailError":
                log.error("Telegram answered 'internal error' for this bot. Run diag.py; if a NEW bot from @BotFather "
                          "logs in fine, this bot's server-side state is broken - use the new bot's token.")
            log.info("retrying Telegram login in %ds", wait)
            await sleep(wait)
            delay = min(delay * 2, 300)


async def main() -> None:
    global CLIENT, UP_LOCK, DL_SEM
    UP_LOCK, DL_SEM = asyncio.Lock(), asyncio.Semaphore(2)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    restore_jobs()
    if not list(BASE.glob(SESSION_NAME + "*")):
        sys.exit("Rubika session not found. Run:  %s login_rubika.py" % sys.executable)
    for attempt in range(1, 6):
        try:
            await RUB.open()
            break
        except Exception as e:
            log.warning("Rubika open failed (%d/5): %s", attempt, short_error(e))
            if attempt == 5:
                sys.exit("Cannot connect to Rubika; check the network / run login_rubika.py again")
            await asyncio.sleep(5 * attempt)

    CLIENT = TelegramClient(str(BASE / "tg_bot"), API_ID, API_HASH, connection_retries=-1, retry_delay=3,
                            auto_reconnect=True, flood_sleep_threshold=60, raise_last_call_error=True)
    await tg_login(CLIENT)
    me = await CLIENT.get_me()
    CLIENT.add_event_handler(on_command, events.NewMessage(incoming=True, func=lambda e: e.is_private and (e.raw_text or "").startswith("/")))
    CLIENT.add_event_handler(on_media, events.NewMessage(incoming=True, func=lambda e: e.is_private and (e.message.file is not None or e.message.photo is not None)))
    CLIENT.add_event_handler(on_text, events.NewMessage(incoming=True, func=lambda e: e.is_private and bool(e.raw_text) and not e.raw_text.startswith("/")))
    CLIENT.add_event_handler(on_button, events.CallbackQuery())
    log.info("ready as @%s (admins: %d)", me.username, len(ADMIN_IDS))
    bg = [asyncio.ensure_future(janitor()), asyncio.ensure_future(watchdog())]
    try:
        await CLIENT.run_until_disconnected()
    finally:
        for t in bg:
            t.cancel()
        await RUB.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
