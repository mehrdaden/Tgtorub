"""Parallel ranged download for Telethon (several in-flight requests = much faster)."""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Callable, Optional

REQUEST_SIZE = 512 * 1024  # Telegram-friendly chunk (multiple of 4 KiB, divides 1 MiB)


async def parallel_download(client, message, dest: Path, size: int, *, workers: int = 4,
                            request_size: int = REQUEST_SIZE,
                            progress: Optional[Callable[[int, int], None]] = None) -> None:
    if size <= 0:
        raise ValueError("unknown file size")
    workers = max(1, min(int(workers), math.ceil(size / request_size)))
    seg = math.ceil(math.ceil(size / workers) / request_size) * request_size
    ranges = [(s, min(s + seg, size)) for s in range(0, size, seg)]

    with open(dest, "wb") as f:
        f.truncate(size)

    done = [0]

    async def worker(start: int, end: int) -> None:
        pos = start
        chunks = math.ceil((end - start) / request_size)
        with open(dest, "r+b") as f:
            async for chunk in client.iter_download(message.media, offset=start, limit=chunks,
                                                    request_size=request_size, file_size=size):
                if pos >= end:
                    break
                data = bytes(chunk)[: end - pos]
                f.seek(pos)
                f.write(data)
                pos += len(data)
                done[0] += len(data)
                if progress:
                    progress(done[0], size)
        if pos != end:
            raise IOError("short download segment %d-%d (got %d bytes)" % (start, end, pos - start))

    tasks = [asyncio.ensure_future(worker(s, e)) for s, e in ranges]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if dest.stat().st_size != size:
        raise IOError("downloaded size mismatch")
