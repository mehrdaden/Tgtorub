"""Small network helpers for the /speed command (stdlib only)."""
from __future__ import annotations

import asyncio
import os
import time
import urllib.request
from typing import Optional, Tuple

UP_URL = "https://speed.cloudflare.com/__up"


def _post(nbytes: int, proxy: Optional[str], timeout: float) -> Tuple[float, float]:
    data = os.urandom(nbytes)
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(UP_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "User-Agent": "tg2rub-speed"})
    t0 = time.monotonic()
    with opener.open(req, timeout=timeout) as r:
        r.read(64)
    el = max(time.monotonic() - t0, 0.001)
    return nbytes / el, el


async def general_upload(nbytes: int = 8 * 1024 * 1024, proxy: Optional[str] = None,
                         timeout: float = 90.0) -> Tuple[float, float]:
    """Raw upload speed of this server to a neutral host: (bytes/s, seconds)."""
    return await asyncio.get_running_loop().run_in_executor(None, _post, nbytes, proxy, timeout)


def verdict(general: Optional[float], single: Optional[float], parallel: Optional[float]) -> str:
    """Plain-language reading of the three measurements (bytes/s, None = not measured/failed)."""
    out = []
    if general and single:
        ratio = single / general
        if ratio < 0.25:
            out.append("روبیکا خیلی کندتر از اینترنت عمومی سرور است (%.0f%%): گلوگاه مسیر سرور↔روبیکا یا خود روبیکاست، نه سرعت سرور." % (ratio * 100))
        else:
            out.append("سرعت روبیکا نزدیک به سرعت عمومی سرور است؛ گلوگاه اصلی پهنای باند خود سرور است.")
    if single and parallel:
        gain = parallel / single
        if gain >= 1.5:
            out.append("اتصال موازی کمک می‌کند (×%.1f): UPLOAD_STREAMS=3 را در .env بگذار و فایل را به چند قسمت تقسیم کن." % gain)
        else:
            out.append("اتصال موازی فایده‌ی محسوسی ندارد (×%.1f)؛ UPLOAD_STREAMS=1 بماند." % gain)
    return "\n".join(out) or "داده‌ی کافی برای نتیجه‌گیری نیست."
