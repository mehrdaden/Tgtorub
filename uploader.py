"""Rubika upload with an automatic multi-strategy retry ladder.

Why a ladder?  Rubika sometimes rejects a file (typically videos) with
SERVER_ERROR / ERROR_TRY_AGAIN.  Plain retrying the identical request often does not
help, so every few attempts we change *how* the file is presented:

  orig  - file exactly as is
  ascii - same bytes, pure-ASCII file name
  bin   - same bytes, neutral '.bin' extension  (Rubika no longer treats it as a video)
  zip   - same bytes inside a stored (uncompressed) ZIP
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

from fileops import ascii_name, link_or_copy, make_zip

log = logging.getLogger("tg2rub.upload")

FATAL_MARKERS = ("INVALID_AUTH", "NOT_REGISTERED", "AUTH_KEY", "SESSION_EXPIRED", "SESSION_REVOKED")


class UploadFailed(Exception):
    def __init__(self, attempts: int, last: Exception):
        super().__init__("upload failed after %d attempts: %s: %s" % (attempts, type(last).__name__, last))
        self.attempts = attempts
        self.last = last


class FatalUploadError(Exception):
    pass


@dataclass
class UploadResult:
    kind: str
    attempt: int
    name: str


def is_fatal(exc: BaseException) -> bool:
    if isinstance(exc, (FileNotFoundError, PermissionError, FatalUploadError)):
        return True
    msg = str(exc).upper()
    return any(m in msg for m in FATAL_MARKERS)


def make_plan(attempts: int, mode: str = "auto") -> List[str]:
    attempts = max(1, int(attempts))
    if mode == "zip":
        base = ["zip"] * attempts
    elif mode == "bin":
        base = ["bin"] * 4 + ["zip"] * max(0, attempts - 4)
    else:
        base = ["orig", "orig", "ascii", "ascii", "ascii", "bin", "bin", "zip", "zip", "zip"]
    if len(base) < attempts:
        base += [base[-1]] * (attempts - len(base))
    return base[:attempts]


def backoff(attempt: int) -> float:
    return min(3 * (2 ** (attempt - 1)), 60) + random.uniform(0, 2)


def _make_variant(path: Path, kind: str, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    safe = ascii_name(path.name)
    if kind == "orig":
        return path
    if kind == "ascii":
        if safe == path.name:
            return path
        dst = workdir / safe
    elif kind == "bin":
        dst = workdir / (safe + ".bin")
    elif kind == "zip":
        dst = workdir / (Path(safe).stem + ".zip")
        if dst.exists():
            dst.unlink()
        if shutil.disk_usage(workdir).free < path.stat().st_size * 1.05 + (64 << 20):
            raise OSError("not enough disk space to build the ZIP fallback")
        make_zip(path, path.name, dst, 0, None)   # stored: fast, no compression
        return dst
    else:
        raise ValueError(kind)
    if dst.exists():
        dst.unlink()
    link_or_copy(path, dst)
    return dst


def _caption(real_name: str, kind: str) -> str:
    if kind == "bin":
        return real_name + "\n[پسوند .bin را حذف کن / remove .bin]"
    if kind == "zip":
        return real_name + "\n[داخل ZIP است / inside ZIP]"
    return real_name


class RubikaSession:
    """Owns the rubpy client; can be re-opened after connection problems."""

    def __init__(self, name: str = "rubika"):
        self.name = name
        self._stack: Optional[AsyncExitStack] = None
        self.client = None

    async def open(self) -> None:
        from rubpy import Client  # lazy: keeps the rest testable without rubpy
        await self.close()
        stack = AsyncExitStack()
        self.client = await stack.enter_async_context(Client(name=self.name))
        self._stack = stack

    async def close(self) -> None:
        stack, self._stack, self.client = self._stack, None, None
        if stack:
            try:
                await asyncio.wait_for(stack.aclose(), 20)
            except Exception as e:  # never let cleanup break a retry
                log.warning("rubika close: %s", type(e).__name__)

    async def reconnect(self) -> None:
        await self.close()
        await self.open()

    async def send_file(self, path: Path, caption: str):
        if self.client is None:
            await self.open()
        fn = getattr(self.client, "send_document", None) or getattr(self.client, "send_file", None)
        if fn is None:
            raise FatalUploadError("installed rubpy has neither send_document nor send_file")
        try:
            return await fn("me", str(path), caption=caption)
        except TypeError as e:
            if "caption" in str(e):
                return await fn("me", str(path))
            raise

    async def send_text(self, text: str):
        if self.client is None:
            await self.open()
        return await self.client.send_message("me", text)


async def upload_part(sender, path: Path, *, attempts: int = 10, mode: str = "auto",
                      timeout: float = 4 * 3600,
                      on_attempt: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
                      on_error: Optional[Callable[[int, str, BaseException], None]] = None,
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> UploadResult:
    """Upload one file; retry up to `attempts` times, changing strategy as per make_plan()."""
    plan = make_plan(attempts, mode)
    workdir = path.parent / ("v_" + path.stem[:20].replace("/", "_"))
    cache: Dict[str, Path] = {}
    last: Optional[Exception] = None
    force_reconnect = False
    try:
        for i, kind in enumerate(plan, 1):
            try:
                if i > 1 and (force_reconnect or i % 3 == 1):
                    try:
                        await sender.reconnect()
                    except Exception as e:
                        log.warning("reconnect failed: %s", e)
                force_reconnect = False
                if kind not in cache:
                    loop = asyncio.get_running_loop()
                    cache[kind] = await loop.run_in_executor(None, _make_variant, path, kind, workdir)
                vpath = cache[kind]
                if on_attempt:
                    await on_attempt(i, len(plan), kind)
                await asyncio.wait_for(sender.send_file(vpath, _caption(path.name, kind)), timeout)
                return UploadResult(kind, i, vpath.name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last = exc
                force_reconnect = isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError))
                log.warning("upload attempt %d/%d (%s) failed: %s: %s", i, len(plan), kind,
                            type(exc).__name__, str(exc)[:300])
                if on_error:
                    on_error(i, kind, exc)
                if is_fatal(exc):
                    raise
                if i < len(plan):
                    await sleep(backoff(i))
        raise UploadFailed(len(plan), last or RuntimeError("unknown"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
