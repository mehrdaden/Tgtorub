"""Blocking, testable file-preparation step (zip / rename / split)."""
from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional

from fileops import link_or_copy, make_zip, split_file


def prepare_outputs(src: Path, name: str, out: Path, *, zip_enabled: bool,
                    password: Optional[str], parts: int, max_part: int,
                    zip_level: int) -> List[Path]:
    """Create the final files to upload inside `out` and return them in order."""
    out.mkdir(parents=True, exist_ok=True)
    if zip_enabled:
        target = out / (name + ".zip")
        make_zip(src, name, target, zip_level, password)
    else:
        target = out / name
        link_or_copy(src, target)
    n = max(1, int(parts))
    size = target.stat().st_size
    if n == 1 and size > max_part:
        n = math.ceil(size / max_part)
    if n > 1:
        files = split_file(target, n)
        target.unlink()
        return files
    return [target]
