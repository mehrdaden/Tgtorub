#!/usr/bin/env python3
"""One-time interactive Rubika login. Session files are kept private."""
import asyncio
import logging
import os
import re
from pathlib import Path

# Keep library diagnostics quiet and redact accidental secrets before any output.
class RedactFilter(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
            msg = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                         "<PRIVATE_KEY_REDACTED>", msg, flags=re.S)
            record.msg, record.args = msg, ()
        except Exception:
            record.msg, record.args = "<log message redacted>", ()
        return True

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
for handler in logging.getLogger().handlers:
    handler.addFilter(RedactFilter())
for name in ("rubpy", "rubpy.network", "rubpy.session", "pyrogram", "pyrogram.session", "pyrogram.crypto"):
    logging.getLogger(name).setLevel(logging.WARNING)

from rubpy import Client

BASE = Path(__file__).resolve().parent
os.umask(0o077)
os.chdir(BASE)


async def main():
    # Opening the context performs login and persists the Rubpy session.
    # Do not send a test message to "me": some Rubpy versions reject that peer.
    async with Client(name="rubika"):
        pass

    # Session may be a file or a directory depending on Rubpy's storage backend.
    for path in BASE.glob("rubika*"):
        if path.is_dir():
            os.chmod(path, 0o700)
            for root, dirs, files in os.walk(path):
                for d in dirs:
                    os.chmod(Path(root) / d, 0o700)
                for f in files:
                    os.chmod(Path(root) / f, 0o600)
        elif path.is_file():
            os.chmod(path, 0o600)
    print("OK - Rubika session saved with restricted permissions.")


if __name__ == "__main__":
    asyncio.run(main())
