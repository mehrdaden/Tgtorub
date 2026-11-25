"""Download links: direct files (multi-connection, resumable) and video/media pages (yt-dlp).

Direct file URL   -> built-in downloader, several parallel connections (stdlib only)
Anything else     -> yt-dlp (YouTube, Instagram, X/Twitter, TikTok, hundreds of other sites)
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import unquote, urlsplit

log = logging.getLogger("tg2rub.links")

URL_RE = re.compile(r"https?://[^\s<>\]\[()\"']+", re.IGNORECASE)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
CHUNK = 256 * 1024
MIN_PARALLEL = 16 * 1024 * 1024


class LinkError(Exception):
    pass


# ------------------------------------------------------------------ URL handling
def _clean(url: str) -> Optional[str]:
    url = url.rstrip(".,!?؛،:;)")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname.rstrip(".").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    return url


def extract_urls(text: str, limit: int = 5) -> List[str]:
    out: List[str] = []
    for m in URL_RE.finditer(text or ""):
        u = _clean(m.group(0))
        if u and u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def extract_url(text: str) -> Optional[str]:
    urls = extract_urls(text, 1)
    return urls[0] if urls else None


def check_public_host(host: str) -> None:
    """Fail closed unless every resolved address is globally routable."""
    host = (host or "").rstrip(".")
    if not host:
        raise LinkError("blocked: URL has no valid host")
    try:
        addrs = {ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(host, None)}
    except (OSError, ValueError) as exc:
        # A DNS failure must not silently disable the private-network check.
        raise LinkError("blocked: could not verify the host DNS address") from exc
    if not addrs or any(not a.is_global for a in addrs):
        raise LinkError("blocked: the address is private, reserved, or not globally routable")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise LinkError("blocked redirect")
        check_public_host(parts.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(proxy: Optional[str]):
    handlers: list = [_SafeRedirect()]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


# ------------------------------------------------------------------ probing
@dataclass
class Probe:
    url: str
    content_type: str
    size: int
    filename: str
    ranges: bool
    is_file: bool


def _filename_from(headers, url: str, content_type: str) -> str:
    cd = headers.get("Content-Disposition", "") or ""
    name = ""
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", cd, re.IGNORECASE)
    if m:
        name = unquote(m.group(1).strip().strip('"'))
    else:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
    if not name:
        name = unquote(os.path.basename(urlsplit(url).path)) or "download"
    name = os.path.basename(name.replace("\\", "/")).strip(". ")
    if not Path(name).suffix:
        ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
        name += ext if ext not in (".bin", ".htm", ".html") else ""
    return name or "download.bin"


def probe_sync(url: str, proxy: Optional[str] = None, timeout: float = 25.0) -> Probe:
    host = urlsplit(url).hostname or ""
    check_public_host(host)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0", "Accept": "*/*"})
    with _opener(proxy).open(req, timeout=timeout) as resp:
        h = resp.headers
        ctype = (h.get("Content-Type", "") or "").lower()
        size, ranges = 0, False
        cr = h.get("Content-Range", "") or ""
        m = re.match(r"bytes\s+\d+-\d+/(\d+)", cr)
        if resp.status == 206 and m:
            size, ranges = int(m.group(1)), True
        else:
            try:
                size = int(h.get("Content-Length", "0") or 0)
            except ValueError:
                size = 0
            ranges = (h.get("Accept-Ranges", "") or "").lower() == "bytes" and size > 0
        final = resp.geturl()
        disp = (h.get("Content-Disposition", "") or "").lower()
        page_like = ctype.startswith(("text/html", "application/xhtml", "text/xml", "application/xml", "application/json"))
        text_like = ctype.startswith("text/") and "attachment" not in disp
        is_file = bool(ctype) and not page_like and not text_like
        return Probe(final, ctype, size, _filename_from(h, final, ctype), ranges, is_file)


# ------------------------------------------------------------------ direct downloader (threads)
def _download_direct(p: Probe, dest: Path, max_bytes: int, connections: int, proxy: Optional[str],
                     progress: Optional[Callable[[int, int], None]], cancel: threading.Event) -> Path:
    if p.size and p.size > max_bytes:
        raise LinkError("file is larger than the configured limit (%d bytes)" % p.size)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / p.filename
    opener = _opener(proxy)
    lock = threading.Lock()
    done = [0]

    def bump(n: int) -> None:
        with lock:
            done[0] += n
            if done[0] > max_bytes:
                raise LinkError("download exceeded the configured size limit")
            if progress:
                progress(done[0], p.size)

    def fetch(start: int, end: Optional[int], f_path: Path, truncate: bool) -> None:
        pos, tries = start, 0
        while True:
            if cancel.is_set():
                raise LinkError("cancelled")
            headers = {"User-Agent": UA, "Accept": "*/*"}
            if end is not None or pos > 0:
                headers["Range"] = "bytes=%d-%s" % (pos, "" if end is None else end)
            try:
                with opener.open(urllib.request.Request(p.url, headers=headers), timeout=30) as r:
                    if "Range" in headers and r.status != 206:
                        if end is not None:
                            raise LinkError("the server stopped honouring Range requests")
                        # single stream resumed on a server that ignores Range: restart from scratch
                        with lock:
                            done[0] = max(0, done[0] - pos)
                        pos = 0
                        truncate = True
                        raise OSError("range not honoured; restarting")
                    with open(f_path, "wb" if truncate else "r+b") as f:
                        truncate = False
                        f.seek(pos)
                        while True:
                            if cancel.is_set():
                                raise LinkError("cancelled")
                            buf = r.read(CHUNK)
                            if not buf:
                                break
                            f.write(buf)
                            pos += len(buf)
                            bump(len(buf))
                            if end is not None and pos > end:
                                break
                if end is None or pos > end:
                    return
                raise OSError("connection closed early")
            except LinkError:
                raise
            except (OSError, urllib.error.URLError, socket.timeout) as e:
                tries += 1
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404, 410):
                    raise LinkError("HTTP %d from the server" % e.code)
                if tries > 6:
                    raise LinkError("download failed: %s" % e)
                time.sleep(min(2 ** tries, 20))

    if p.ranges and p.size >= MIN_PARALLEL and connections > 1:
        with open(target, "wb") as f:
            f.truncate(p.size)
        seg = -(-p.size // connections)
        spans = [(s, min(s + seg, p.size) - 1) for s in range(0, p.size, seg)]
        with ThreadPoolExecutor(max_workers=len(spans)) as ex:
            futs = [ex.submit(fetch, s, e, target, False) for s, e in spans]
            try:
                for fu in futs:
                    fu.result()
            except BaseException:
                cancel.set()
                raise
    else:
        fetch(0, None, target, True)
    if target.stat().st_size <= 0 or (p.size and target.stat().st_size != p.size):
        raise LinkError("incomplete download")
    return target


# ------------------------------------------------------------------ yt-dlp
def _friendly(lines: List[str]) -> str:
    """Translate the most useful yt-dlp diagnostics into a short Persian message."""
    text = "\n".join(lines[-16:])
    low = text.lower()
    if "sign in to confirm" in low or "not a bot" in low or "login required" in low:
        return "سایت برای ورود یا تأیید انسانی محدودیت گذاشته است. فقط کوکی تازه و معتبر حساب خودت را در COOKIES_FILE تنظیم کن."
    if "age-restricted" in low or "confirm your age" in low or "age restricted" in low:
        return "این محتوا محدودیت سنی دارد؛ با دسترسی مجاز و کوکی معتبر حساب خودت امتحان کن."
    if "private" in low or "members-only" in low or "subscriber-only" in low:
        return "این محتوا خصوصی یا مخصوص اعضاست و با دسترسی فعلی قابل دریافت نیست."
    if "po token" in low or "pot provider" in low or "proof of origin" in low:
        return "یوتیوب برای این درخواست PO Token می‌خواهد. yt-dlp و Deno را به‌روز نگه دار؛ در صورت تداوم 403 باید PO Token Provider سازگار و جداگانه راه‌اندازی شود."
    if "sabr" in low or ("403" in low and "youtube" in low):
        return "یوتیوب دریافت این فرمت را با 403/SABR رد کرده است؛ ممکن است محدودیت IP یا نیاز به PO Token باشد."
    if "unsupported url" in low:
        return "این سایت یا قالب لینک توسط yt-dlp پشتیبانی نمی‌شود."
    if "429" in low or "too many requests" in low:
        return "سایت تعداد درخواست‌ها را محدود کرده (429). کمی صبر کن و دوباره امتحان کن."
    if "javascript runtime" in low or "js runtime" in low or "challenge solving failed" in low or "ejs" in low:
        return "چالش جاوااسکریپت حل نشد؛ Deno نسخه 2.3 یا بالاتر و بسته هم‌نسخه yt-dlp-ejs را بررسی کن."
    if "requested format is not available" in low or "no video formats" in low:
        return "فرمت قابل دریافت پیدا نشد؛ کیفیت YTDLP_MAX_HEIGHT را کمتر کن یا کوکی معتبر تنظیم کن."
    if "404" in low or "not found" in low or "video unavailable" in low:
        return "محتوا حذف شده، لینک اشتباه است یا در منطقه/حساب فعلی در دسترس نیست."
    if not text:
        return "دانلودر خطای نامشخص داد؛ /diag و لاگ سرویس را بررسی کن."
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"https?://\S+", "[URL]", text)
    return "جزئیات فنی: " + " | ".join(x.strip() for x in text.splitlines()[-3:] if x.strip())[:320]


def _is_youtube(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def _youtube_fallback_worth_trying(lines: List[str]) -> bool:
    """Retry only extraction/delivery failures for which a different YT client can help."""
    low = "\n".join(lines).lower()
    if any(x in low for x in ("sign in to confirm", "not a bot", "age-restricted", "private video",
                               "members-only", "video unavailable", "copyright")):
        return False
    return any(x in low for x in ("403", "sabr", "po token", "requested format is not available",
                                  "no video formats", "javascript challenge", "challenge solving failed",
                                  "فرمت قابل دریافت پیدا نشد", "چالش جاوااسکریپت حل نشد"))


def js_runtime_status() -> str:
    """Return a concise, version-checked JavaScript runtime status for /diag."""
    old = []
    for name, minimum in (("deno", (2, 3, 0)), ("node", (22, 0, 0))):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5, check=True).stdout
            m = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", out)
            if not m:
                continue
            version = tuple(map(int, m.groups()))
            if version >= minimum:
                return "%s %s" % (name.capitalize(), ".".join(map(str, version)))
            old.append("%s %s" % (name.capitalize(), ".".join(map(str, version))))
        except Exception:
            continue
    if old:
        return "قدیمی: " + ", ".join(old) + " (Deno >=2.3 یا Node >=22 لازم است)"
    return "موجود نیست (Deno >=2.3 یا Node >=22 لازم است)"


def _js_runtime_args() -> List[str]:
    """Select a supported runtime explicitly when one is available on PATH."""
    deno = shutil.which("deno")
    if deno:
        try:
            out = subprocess.run([deno, "--version"], capture_output=True, text=True, timeout=5, check=True).stdout
            m = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", out)
            if m and tuple(map(int, m.groups())) >= (2, 3, 0):
                return ["--js-runtimes", "deno:" + deno]
        except Exception:
            pass
    node = shutil.which("node")
    if node:
        try:
            out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5, check=True).stdout
            m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", out)
            if m and tuple(map(int, m.groups())) >= (22, 0, 0):
                return ["--js-runtimes", "node:" + node]
        except Exception:
            pass
    return []


async def _ytdlp_once(url: str, attempt_dest: Path, max_bytes: int, progress, *, proxy, cookies, height,
                      youtube_client: Optional[str]) -> Path:
    attempt_dest.mkdir(parents=True, exist_ok=True)
    have_ffmpeg = shutil.which("ffmpeg") is not None
    cmd = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--newline", "--no-cache-dir",
           "--retries", "4", "--fragment-retries", "5", "--concurrent-fragments", "3",
           "--socket-timeout", "30", "--max-filesize", str(max_bytes),
           "--progress", "--progress-template",
           "download:TG2R|%(progress.downloaded_bytes)s|%(progress.total_bytes_estimate)s|%(progress.total_bytes)s",
           "--output", str(attempt_dest / "%(title).150B [%(id)s].%(ext)s")]
    cmd += _js_runtime_args()
    if have_ffmpeg:
        cmd += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]
        if height:
            cmd += ["-S", "res:%d,ext" % height]
    else:
        cmd += ["-f", "b"]
    if youtube_client:
        cmd += ["--extractor-args", "youtube:player_client=" + youtube_client]
    if proxy:
        cmd += ["--proxy", proxy]
    if cookies and Path(cookies).is_file():
        cmd += ["--cookies", cookies]
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    lines: List[str] = []
    try:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("TG2R|"):
                f = line.split("|")
                try:
                    done = int(float(f[1]))
                except (ValueError, IndexError):
                    continue
                total = 0
                estimated = False
                try:
                    total = int(float(f[3])) if len(f) > 3 and f[3] else 0
                except (ValueError, TypeError):
                    total = 0
                if total <= 0:
                    try:
                        total = int(float(f[2])) if len(f) > 2 and f[2] else 0
                        estimated = total > 0
                    except (ValueError, TypeError):
                        total = 0
                if progress:
                    progress(done, -total if estimated else total)
                if done > max_bytes:
                    raise LinkError("download exceeded the configured size limit")
            elif line:
                # Warnings are deliberately retained: YouTube's PO-token/SABR warnings
                # are essential to distinguish an extractor problem from a network failure.
                lines = (lines + [line[-500:]])[-24:]
        rc = await proc.wait()
        if rc != 0:
            raise LinkError(_friendly(lines))
    except BaseException:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        raise
    created = [p for p in attempt_dest.iterdir() if p.is_file()
               and not p.name.endswith((".part", ".ytdl", ".temp")) and ".part-" not in p.name]
    if not created:
        raise LinkError(_friendly(lines) if lines else "No downloadable file was produced")
    created.sort(key=lambda p: p.stat().st_size, reverse=True)
    res = created[0]
    if res.stat().st_size <= 0 or res.stat().st_size > max_bytes:
        raise LinkError("downloaded file is empty or exceeds the configured size limit")
    return res


async def _ytdlp(url: str, dest: Path, max_bytes: int, progress, *, proxy, cookies, height) -> Path:
    """Run yt-dlp; retry YouTube once with web_safari only for relevant delivery errors."""
    dest.mkdir(parents=True, exist_ok=True)
    attempts = [None]
    if _is_youtube(url):
        attempts.append("web_safari")
    last_error: Optional[Exception] = None
    for idx, client in enumerate(attempts):
        attempt_dir = dest / (".ytdlp-attempt-%d" % (idx + 1))
        shutil.rmtree(attempt_dir, ignore_errors=True)
        try:
            result = await _ytdlp_once(url, attempt_dir, max_bytes, progress, proxy=proxy,
                                       cookies=cookies, height=height, youtube_client=client)
            target = dest / result.name
            if target.exists():
                stem, suffix = target.stem, target.suffix
                n = 2
                while target.exists():
                    target = dest / ("%s (%d)%s" % (stem, n, suffix))
                    n += 1
            shutil.move(str(result), str(target))
            return target
        except LinkError as e:
            last_error = e
            shutil.rmtree(attempt_dir, ignore_errors=True)
            if idx + 1 >= len(attempts):
                if client == "web_safari":
                    raise LinkError("تلاش جایگزین web_safari نیز ناموفق بود. " + str(e)) from e
                raise
            if not _youtube_fallback_worth_trying([str(e)]):
                raise
            log.warning("YouTube default extraction failed; retrying once with web_safari client")
        except BaseException:
            shutil.rmtree(attempt_dir, ignore_errors=True)
            raise
    raise LinkError(str(last_error or "yt-dlp failed"))


# ------------------------------------------------------------------ public entry point
async def download_url(url: str, dest: Path, max_bytes: int,
                       progress: Optional[Callable[[int, int], None]] = None, *,
                       proxy: Optional[str] = None, cookies: Optional[str] = None,
                       connections: int = 4, max_height: int = 1080) -> Path:
    """Download one item. Direct files use the built-in multi-connection downloader, pages use yt-dlp."""
    loop = asyncio.get_running_loop()
    host = urlsplit(url).hostname or ""
    if not proxy:
        await loop.run_in_executor(None, check_public_host, host)
    probe: Optional[Probe] = None
    try:
        probe = await loop.run_in_executor(None, probe_sync, url, proxy)
    except LinkError:
        raise
    except Exception:
        probe = None                              # HEAD/Range probes are blocked on some sites: let yt-dlp try
    if probe is not None and probe.is_file:
        cancel = threading.Event()
        try:
            return await loop.run_in_executor(None, _download_direct, probe, dest, max_bytes,
                                              max(1, connections), proxy, progress, cancel)
        except asyncio.CancelledError:
            cancel.set()
            raise
    return await _ytdlp(url, dest, max_bytes, progress, proxy=proxy, cookies=cookies, height=max_height)
