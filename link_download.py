"""Download public media/file URLs through yt-dlp with bounded size and cancellation."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

URL_RE = re.compile(r"https?://[^\s<>\]\[()\"']+", re.IGNORECASE)


def extract_url(text: str) -> Optional[str]:
    match = URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(0).rstrip(".,!?؛،:)")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    # The bot is admin-only, but still reject obvious local/private targets.
    host = parts.hostname.rstrip(".").lower()
    if host in ("localhost", "localhost.localdomain"):
        return None
    try:
        addresses = {ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(host, None)}
        if addresses and any(not a.is_global for a in addresses):
            return None
    except (OSError, ValueError):
        # DNS may be temporarily unavailable; yt-dlp will report the useful error.
        pass
    return url


async def download_url(url: str, dest: Path, max_bytes: int,
                       progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """Download one item (not a playlist). Raises on unsupported/blocked URLs."""
    dest.mkdir(parents=True, exist_ok=True)
    before = set(dest.iterdir())
    cmd = [
        __import__("sys").executable, "-m", "yt_dlp",
        "--no-playlist", "--newline", "--progress", "--no-cache-dir",
        "--no-warnings",
        "--retries", "5", "--fragment-retries", "5",
        "--socket-timeout", "30", "--max-filesize", str(max_bytes),
        "--progress-template",
        "download:TG2R|%(progress.downloaded_bytes)s|%(progress.total_bytes_estimate)s|%(progress.total_bytes)s",
        "--output", str(dest / "%(title).150B [%(id)s].%(ext)s"),
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    lines = []
    try:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("TG2R|"):
                fields = line.split("|")
                try:
                    done = int(float(fields[1]))
                except (ValueError, IndexError):
                    continue
                total = 0
                for candidate in (fields[3] if len(fields) > 3 else "", fields[2] if len(fields) > 2 else ""):
                    try:
                        total = int(float(candidate))
                        break
                    except (ValueError, TypeError):
                        continue
                if progress:
                    progress(done, total)
                if done > max_bytes:
                    raise ValueError("download exceeded configured MAX_FILE_GB")
            elif line:
                lines.append(line[-500:])
                lines = lines[-12:]
        code = await proc.wait()
        if code != 0:
            raise RuntimeError("\n".join(lines[-6:]) or "yt-dlp failed (unsupported URL, login required, or site blocked)")
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        raise
    except BaseException:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        raise

    created = [p for p in dest.iterdir() if p not in before and p.is_file()
               and not p.name.endswith((".part", ".ytdl", ".temp"))]
    if not created:
        raise RuntimeError("\n".join(lines[-6:]) or "No downloadable file was produced")
    # --no-playlist normally creates one file; reject ambiguous multi-file output.
    created.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    result = created[0]
    if result.stat().st_size <= 0 or result.stat().st_size > max_bytes:
        raise RuntimeError("downloaded file is empty or exceeds configured size limit")
    return result
