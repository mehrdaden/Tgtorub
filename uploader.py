"""Rubika upload with an automatic multi-strategy retry ladder and a slow-upload watchdog.

Why a ladder?  Rubika sometimes rejects a file (typically videos / unusual extensions) with
SERVER_ERROR / ERROR_TRY_AGAIN.  Plain retrying the identical request often does not help,
so every few attempts we change *how* the file is presented:

  orig  - file exactly as is
  ascii - same bytes, pure-ASCII file name
  bin   - same bytes, neutral '.bin' extension  (Rubika no longer treats it as a video)
  zip   - same bytes inside a stored (uncompressed) ZIP

Slow-upload watchdog: upload speed is measured from the server's real outgoing network
traffic (/proc/net/dev).  If it stays under `min_speed` for a whole window the attempt is
cancelled and retried on a fresh connection - a stalled/throttled connection often recovers
on a new one.  This is limited to `max_aborts` per part; after that the upload is allowed to
finish slowly (only a near-zero speed for several minutes is still treated as a hang).
"""
from __future__ import annotations

import asyncio
import collections
import logging
import random
import shutil
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fileops import ascii_name, link_or_copy, make_zip

log = logging.getLogger("tg2rub.upload")

FATAL_MARKERS = ("INVALID_AUTH", "NOT_REGISTERED", "AUTH_KEY", "SESSION_EXPIRED", "SESSION_REVOKED")
PEER_MARKERS = ("INVALID_INPUT", "OBJECT_GUID", "INVALID_GUID", "PEER", "NOT_FOUND")


class UploadFailed(Exception):
    def __init__(self, attempts: int, last: Exception):
        super().__init__("upload failed after %d attempts: %s: %s" % (attempts, type(last).__name__, last))
        self.attempts = attempts
        self.last = last


class FatalUploadError(Exception):
    pass


class StallError(Exception):
    def __init__(self, rate: float, window: float):
        super().__init__("upload too slow: %.0f B/s over %.0fs" % (rate, window))
        self.rate = rate


@dataclass
class UploadResult:
    kind: str
    attempt: int
    name: str
    seconds: float = 0.0


@dataclass
class StallPolicy:
    min_speed: float = 60 * 1024      # bytes/s
    window: float = 90.0              # seconds the speed must stay below min_speed
    grace: float = 45.0               # ignore the start of every attempt (connection setup)
    max_aborts: int = 3               # after this many aborts, stop aborting slow-but-moving uploads
    enabled: bool = True
    aborts: int = 0

    def current(self):
        """(min_speed, window) - strict while aborts remain, 'hang detection only' afterwards."""
        if self.enabled and self.aborts < self.max_aborts:
            return self.min_speed, self.window
        return 2 * 1024.0, 300.0


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


def tx_bytes() -> Optional[int]:
    """Total bytes sent by the server's network interfaces (Linux)."""
    try:
        total = 0
        with open("/proc/net/dev") as f:
            for line in list(f)[2:]:
                name, _, rest = line.partition(":")
                if name.strip() == "lo":
                    continue
                total += int(rest.split()[8])
        return total
    except Exception:
        return None


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


def _dig(obj: Any, path) -> Any:
    for key in path:
        if obj is None:
            return None
        obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    return obj


class RubikaSession:
    """Owns the rubpy client; can be re-opened after connection problems."""

    def __init__(self, name: str = "rubika", target: str = "me"):
        self.name = name
        self.target = target or "me"
        self._stack: Optional[AsyncExitStack] = None
        self._lock: Optional[asyncio.Lock] = None
        self.client = None
        self.inflight = 0

    async def open(self) -> None:
        from rubpy import Client  # lazy: keeps the rest testable without rubpy
        if self._lock is None:
            self._lock = asyncio.Lock()
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
        if self.inflight > 0:          # other uploads are running: do not kill them
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.inflight > 0:
                return
            await self.close()
            await self.open()

    async def _own_guid(self) -> Optional[str]:
        c = self.client
        for attr in ("guid", "user_guid"):
            v = getattr(c, attr, None)
            if isinstance(v, str) and v:
                return v
        fn = getattr(c, "get_me", None)
        if fn is None:
            return None
        me = await fn()
        for path in (("user", "user_guid"), ("user_guid",), ("guid",), ("user", "guid")):
            v = _dig(me, path)
            if isinstance(v, str) and v:
                return v
        return None

    async def send_file(self, path: Path, caption: str):
        if self.client is None:
            await self.open()
        self.inflight += 1
        try:
            return await self._send(path, caption)
        finally:
            self.inflight -= 1

    async def _send(self, path: Path, caption: str):
        """Upload from disk in chunks, then send the returned file metadata.

        Do not pass str(path) to send_document/send_file: in Rubpy 7.3.5
        send_message reads a local path completely into memory before upload.
        client.upload(str(path)) uses the chunked upload_file implementation.
        """
        if not path.is_file():
            raise FileNotFoundError(str(path))

        upload_fn = getattr(self.client, "upload", None)
        send_fn = getattr(self.client, "send_message", None)
        if upload_fn is None or send_fn is None:
            raise FatalUploadError(
                "installed rubpy must provide upload and send_message"
            )

        async def call(target: str):
            uploaded = await upload_fn(str(path), file_name=path.name)
            if uploaded is None:
                raise RuntimeError("Rubpy upload returned no file metadata")
            return await send_fn(
                target,
                text=caption,
                file_inline=uploaded,
            )

        try:
            return await call(self.target)
        except Exception as e:
            # Some rubpy versions reject the 'me' alias for file messages:
            # retry with our own GUID if it can be determined.
            if self.target == "me" and any(m in str(e).upper() for m in PEER_MARKERS):
                guid = await self._own_guid()
                if guid:
                    res = await call(guid)
                    self.target = guid
                    log.info("Rubika: using own GUID instead of 'me'")
                    return res
            raise

    async def send_text(self, text: str):
        if self.client is None:
            await self.open()
        try:
            return await self.client.send_message(self.target, text)
        except Exception:
            if self.target == "me":
                guid = await self._own_guid()
                if guid:
                    return await self.client.send_message(guid, text)
            raise


async def _send_watched(sender, vpath: Path, caption: str, timeout: float, policy: StallPolicy,
                        on_progress: Optional[Callable[[int, float], None]], interval: float,
                        monitor: bool) -> Any:
    """Run one upload attempt; abort it if the real upload speed collapses."""
    task = asyncio.ensure_future(sender.send_file(vpath, caption))
    t0 = time.monotonic()
    tx0 = tx_bytes()
    samples: "collections.deque" = collections.deque()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                return task.result()
            now = time.monotonic()
            if now - t0 > timeout:
                raise asyncio.TimeoutError("attempt timed out after %.0fs" % timeout)
            tx = tx_bytes()
            if tx is None or tx0 is None:
                continue
            if on_progress:
                on_progress(max(0, tx - tx0), now - t0)
            if not monitor:
                continue
            min_speed, window = policy.current()
            samples.append((now, tx))
            while samples and now - samples[0][0] > window:
                samples.popleft()
            if now - t0 >= policy.grace and now - samples[0][0] >= window * 0.9:
                rate = (tx - samples[0][1]) / max(now - samples[0][0], 0.1)
                if rate < min_speed:
                    policy.aborts += 1
                    raise StallError(rate, now - samples[0][0])
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def upload_part(sender, path: Path, *, attempts: int = 10, mode: str = "auto",
                      timeout: float = 4 * 3600,
                      policy: Optional[StallPolicy] = None, monitor: bool = True,
                      poll_interval: float = 3.0,
                      on_attempt: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
                      on_progress: Optional[Callable[[int, float], None]] = None,
                      on_error: Optional[Callable[[int, str, BaseException], None]] = None,
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> UploadResult:
    """Upload one file; retry up to `attempts` times, changing strategy as per make_plan()."""
    plan = make_plan(attempts, mode)
    policy = policy or StallPolicy(enabled=False)
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
                t_start = time.monotonic()
                await _send_watched(sender, vpath, _caption(path.name, kind), timeout, policy,
                                    on_progress, poll_interval, monitor)
                return UploadResult(kind, i, vpath.name, time.monotonic() - t_start)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last = exc
                force_reconnect = isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError, StallError))
                log.warning("upload attempt %d/%d (%s) failed: %s: %s", i, len(plan), kind,
                            type(exc).__name__, str(exc)[:300])
                if on_error:
                    on_error(i, kind, exc)
                if is_fatal(exc):
                    raise
                if i < len(plan):
                    await sleep(1.0 if isinstance(exc, StallError) else backoff(i))
        raise UploadFailed(len(plan), last or RuntimeError("unknown"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
