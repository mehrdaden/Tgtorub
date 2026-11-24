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
from link_download import download_url, extract_url  # noqa: E402
from fileops import safe_name  # noqa: E402
from pipeline import prepare_outputs  # noqa: E402
from uploader import FatalUploadError, RubikaSession, UploadFailed, upload_part  # noqa: E402

CLIENT: Optional[TelegramClient] = None
RUB = RubikaSession(SESSION_NAME)
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


def bar(pct: float, width: int = 12) -> str:
    pct = max(0.0, min(100.0, pct))
    full = int(round(width * pct / 100))
    return "▓" * full + "░" * (width - full) + " %d%%" % int(pct)


def progress_text(title: str, done: float, total: float, t0: float, approx: bool = False) -> str:
    el = max(time.monotonic() - t0, 0.1)
    if total <= 0:
        return "%s\n⏱ %s" % (title, fmt_time(el))
    pct = min(done * 100.0 / total, 99.0 if approx else 100.0)
    speed = done / el
    eta = (total - done) / speed if speed > 0 else 0
    pre = "≈ " if approx else ""
    return "%s\n%s%s\n%s%s / %s\n⚡ %s/s   ⏳ %s   ⏱ %s" % (
        title, pre, bar(pct), pre, human(min(done, total)), human(total), human(speed), fmt_time(eta), fmt_time(el))


def short_error(exc: BaseException) -> str:
    s = "%s: %s" % (type(exc).__name__, str(exc))
    s = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "<TOKEN>", s)
    return s.replace("\n", " ")[:300]


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
        await show(j, "❌ دریافت ناموفق بود:\n" + short_error(e))
        return
    finally:
        tick.cancel()
    await show(j, card(j), keyboard(j))
    if AUTO_UPLOAD:
        start_upload(j)


async def download_link_job(j: Job, url: str) -> None:
    t0 = time.monotonic()
    tick = asyncio.ensure_future(ticker(
        j, lambda: progress_text("⏬ دانلود لینک\n" + j.name, j.progress[0], j.progress[1], t0)
    ))

    def cb(cur: int, total: int) -> None:
        j.progress = (cur, total)

    try:
        async with DL_SEM:
            downloaded = await download_url(url, j.dir / "link", MAX_FILE, progress=cb)
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
        log.exception("link download failed")
        drop(j)
        await show(j, "❌ دانلود لینک ناموفق بود:\n" + short_error(e))
        return
    finally:
        tick.cancel()
        shutil.rmtree(j.dir / "link", ignore_errors=True)
    await show(j, "✅ دانلود کامل شد.\n" + card(j), keyboard(j))
    if AUTO_UPLOAD:
        start_upload(j)


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
            notes: List[str] = []
            for idx, p in enumerate(files, 1):
                if p.name in j.sent:
                    p.unlink()
                    continue
                size = p.stat().st_size
                state = {"attempt": 1, "of": UPLOAD_ATTEMPTS, "kind": "orig", "t0": time.monotonic()}

                def render(p=p, size=size, idx=idx, state=state) -> str:
                    head = "📤 آپلود به روبیکا — قسمت %d/%d\\n%s\\n📦 حجم: %s\\n🔁 تلاش %d/%d (%s)" % (
                        idx, total_parts, p.name, human(size), state["attempt"], state["of"], state["kind"])
                    # rubpy exposes no trustworthy per-file progress callback through this wrapper.
                    return head + "\\n⏱ زمان تلاش: " + fmt_time(time.monotonic() - state["t0"])

                async def on_attempt(i, of, kind, state=state):
                    state.update(attempt=i, of=of, kind=kind, t0=time.monotonic())
                    await show(j, render(), cancel_kb(j))

                tick = asyncio.ensure_future(ticker(j, render))
                try:
                    res = await upload_part(RUB, p, attempts=UPLOAD_ATTEMPTS, mode=j.mode, timeout=UPLOAD_TIMEOUT,
                                            on_attempt=on_attempt,
                                            on_error=lambda i, k, e: ERRORS.append("upload[%s #%d]: %s" % (k, i, short_error(e))))
                finally:
                    tick.cancel()
                elapsed_part = max(0.1, time.monotonic() - state["t0"])
                average_rate = size / elapsed_part
                notes.append("• %s: میانگین سرعت تلاش موفق ≈ %s/s" % (p.name, human(average_rate)))
                if res.kind != "orig":
                    notes.append("  روش جایگزین: %s (%s)" % (
                        {"ascii": "نام ASCII", "bin": ".bin", "zip": "داخل ZIP"}.get(res.kind, res.kind), res.name))
                j.sent.append(p.name)
                persist(j)
                p.unlink()

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
            await show(j, "❌ خطا: %s%s\n\n%s" % (reason, hint, card(j)), keyboard(j))
    finally:
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
        url = extract_url(event.raw_text or "")
        if not url:
            return
        if shutil.disk_usage(WORK_DIR).free < (256 << 20):
            await event.reply("❌ فضای دیسک سرور کافی نیست.")
            return
        jid = secrets.token_hex(4)
        j = Job(id=jid, dir=WORK_DIR / jid, name="در حال دریافت لینک", size=0,
                chat_id=event.chat_id, user_id=event.sender_id, state="downloading")
        j.dir.mkdir(parents=True)
        status = await event.reply("🔗 لینک دریافت شد؛ در حال بررسی و دانلود…",
                                   buttons=cancel_kb(j))
        j.msg_id = status.id
        JOBS[jid] = j
        j.task = asyncio.ensure_future(download_link_job(j, url))
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
HELP = ("سلام 👋\nفایل بفرست یا لینک مستقیم/صفحه ویدئو از سایت‌های پشتیبانی‌شده را ارسال کن؛ بعد با دکمه‌ها نام، ZIP، رمز و "
        "تقسیم را تنظیم کن و «🚀 آپلود به روبیکا» را بزن. درصد پیشرفت دانلود و آپلود نمایش داده می‌شود.\n\n"
        "/list فایل‌های روی سرور\n/disk فضای دیسک\n/clean حذف همه فایل‌ها از سرور\n"
        "/test تست اتصال روبیکا\n/diag وضعیت و آخرین خطاها\n/cancel لغو ورودی در حال انتظار")


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
            await event.reply("❌ " + short_error(e))
    elif cmd == "diag":
        from importlib import metadata

        def ver(p):
            try:
                return metadata.version(p)
            except Exception:
                return "?"
        d = shutil.disk_usage(WORK_DIR)
        await event.reply("🩺 tg2rub\nPython %s\ntelethon %s | rubpy %s | yt-dlp %s\nدیسک آزاد: %s\nجاب‌ها: %d\n"
                          "تلاش آپلود: %d | حالت: %s | دانلود موازی: %d\n\nآخرین خطاها:\n%s" % (
                              sys.version.split()[0], ver("telethon"), ver("rubpy"), ver("yt-dlp"), human(d.free), len(JOBS),
                              UPLOAD_ATTEMPTS, UPLOAD_MODE, DOWNLOAD_WORKERS, "\n".join(ERRORS) or "—"))
    elif cmd == "cancel":
        AWAIT.pop(event.sender_id, None)
        await event.reply("لغو شد.")


# ------------------------------------------------------------------ lifecycle
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
