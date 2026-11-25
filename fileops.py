"""Pure, tested file operations used by the tg2rub bridge."""
from __future__ import annotations

import math
import os
import re
import shutil
import zipfile
from pathlib import Path

try:
    import pyzipper
except ImportError:  # unencrypted ZIP remains available if optional AES package is absent
    pyzipper = None

BUF = 16 * 1024 * 1024


def safe_name(s: str, fallback: str = "file") -> str:
    s = os.path.basename(str(s).replace("\\", "/")).strip()
    s = re.sub(r'[\x00-\x1f<>:"|?*]', "_", s).strip(". ")
    return s[:150] or fallback


def make_zip(src: Path, arcname: str, dst: Path, zip_level: int = 0, password: str | None = None) -> None:
    if not 0 <= int(zip_level) <= 9:
        raise ValueError("zip_level must be between 0 and 9")
    if int(zip_level) == 0:
        comp, kw = zipfile.ZIP_STORED, {}
    else:
        comp, kw = zipfile.ZIP_DEFLATED, {"compresslevel": int(zip_level)}
    if password is None:
        with zipfile.ZipFile(dst, "w", comp, allowZip64=True, **kw) as z:
            z.write(src, arcname)
        return
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
        raise ValueError("ZIP password must contain 8 to 128 characters")
    if pyzipper is None:
        raise RuntimeError("AES-256 ZIP requires pyzipper; install project requirements first")
    # WinZip AES-256 encrypts file contents. ZIP entry names remain visible.
    with pyzipper.AESZipFile(dst, "w", compression=comp,
                             allowZip64=True, **kw) as z:
        z.setpassword(password.encode("utf-8"))
        z.setencryption(pyzipper.WZ_AES, nbits=256)
        z.write(src, arcname)


def link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def split_file(path: Path, n: int) -> list[Path]:
    """Split into at most n non-empty parts and verify every byte was written."""
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("cannot split an empty file")
    if n < 1:
        raise ValueError("part count must be at least one")
    chunk = math.ceil(size / n)
    outs: list[Path] = []
    left_total = size
    try:
        with open(path, "rb") as f:
            for i in range(1, n + 1):
                if left_total <= 0:
                    break
                part = path.with_name(f"{path.name}.{i:03d}")
                expected = min(chunk, left_total)
                written = 0
                with open(part, "wb") as o:
                    while written < expected:
                        block = f.read(min(BUF, expected - written))
                        if not block:
                            raise IOError("unexpected end of source while splitting")
                        count = o.write(block)
                        if count != len(block):
                            raise IOError("short write while splitting")
                        written += count
                if part.stat().st_size != expected:
                    raise IOError("split part size verification failed")
                left_total -= written
                outs.append(part)
        if left_total != 0 or sum(x.stat().st_size for x in outs) != size:
            raise IOError("split output size verification failed")
        return outs
    except Exception:
        for item in outs:
            item.unlink(missing_ok=True)
        for item in path.parent.glob(path.name + ".[0-9][0-9][0-9]"):
            item.unlink(missing_ok=True)
        raise


_VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".3gp", ".mpg", ".mpeg"}


def is_video_name(name: str) -> bool:
    return Path(name).suffix.lower() in _VIDEO_EXT


def ascii_name(name: str, fallback: str = "file") -> str:
    """Pure-ASCII, shell/URL-safe version of a file name (keeps the extension)."""
    p = Path(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", p.stem).strip("._-")[:60] or fallback
    ext = re.sub(r"[^A-Za-z0-9.]+", "", p.suffix)[:10]
    return stem + ext
