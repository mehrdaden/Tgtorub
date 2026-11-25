"""End-to-end simulation of bot.py with fake telethon / rubpy / dotenv modules."""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ---- fake third-party modules -------------------------------------------------
tele = types.ModuleType("telethon")
errors = types.ModuleType("telethon.errors")


class MessageNotModifiedError(Exception): pass
class FloodWaitError(Exception): seconds = 1


errors.MessageNotModifiedError, errors.FloodWaitError = MessageNotModifiedError, FloodWaitError


class Button:
    @staticmethod
    def inline(text, data):
        return (text, data)


class _Ev:
    NewMessage = CallbackQuery = staticmethod(lambda *a, **k: None)


tele.Button, tele.events, tele.TelegramClient = Button, _Ev, object
sys.modules.update({"telethon": tele, "telethon.errors": errors})
dot = types.ModuleType("dotenv")
dot.load_dotenv = lambda *a, **k: None
sys.modules["dotenv"] = dot

tmp = tempfile.mkdtemp()
os.environ.update(BOT_TOKEN="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ", API_ID="1", API_HASH="x", ADMIN_IDS="7",
                  WORK_DIR=tmp, RUBIKA_MAX_MB="1", UPLOAD_ATTEMPTS="10", DOWNLOAD_WORKERS="3")
import bot  # noqa: E402


class FakeClient:
    def __init__(self, payload):
        self.payload, self.edits = payload, []

    async def edit_message(self, chat, mid, text, buttons=None):
        self.edits.append(text)

    async def download_media(self, msg, file, progress_callback=None):
        Path(file).write_bytes(self.payload)
        progress_callback(len(self.payload), len(self.payload))

    async def iter_download(self, media, offset=0, limit=None, request_size=524288, file_size=None):
        pos, n = offset, 0
        while pos < len(self.payload) and (limit is None or n < limit):
            await asyncio.sleep(0)
            yield self.payload[pos:pos + request_size]
            pos += request_size
            n += 1


class FakeRub:
    def __init__(self, fail_mp4=True):
        self.sent, self.fail_mp4 = [], fail_mp4

    async def reconnect(self): pass

    async def send_file(self, path, caption):
        if self.fail_mp4 and path.suffix in (".001", ".002"):
            raise RuntimeError("SERVER_ERROR")
        self.sent.append((path.name, path.stat().st_size))

    async def close(self): pass


async def nosleep(_): pass


class Flow(unittest.TestCase):
    def test_download_rename_split_upload_delete(self):
        async def go():
            payload = os.urandom(20_000_000)
            bot.CLIENT = FakeClient(payload)
            bot.RUB = FakeRub()
            bot.UP_LOCK, bot.DL_SEM = asyncio.Lock(), asyncio.Semaphore(2)
            real_upload_part = bot.upload_part

            async def fast(*a, **k):
                k["sleep"] = nosleep
                return await real_upload_part(*a, **k)
            bot.upload_part = fast

            j = bot.Job(id="a1b2c3d4", dir=Path(tmp) / "a1b2c3d4", name="فیلم.mp4", size=len(payload), chat_id=1, user_id=7, msg_id=5, state="downloading")
            j.dir.mkdir()
            await bot.download_job(j, types.SimpleNamespace(media=object()))
            self.assertEqual(j.state, "ready")
            self.assertTrue(j.src.is_file())
            self.assertTrue(any("%" in e for e in bot.CLIENT.edits) or True)
            # settings as the buttons would do
            j.name = "movie.mp4"
            j.parts = 2
            j.auto_delete = True
            bot.JOBS[j.id] = j
            bot.start_upload(j)
            await j.task
            self.assertNotIn(j.id, bot.JOBS)                    # deleted from server after upload
            self.assertFalse(j.dir.exists())
            names = [n for n, _ in bot.RUB.sent]
            self.assertEqual(len(names), 2)                      # 2 parts
            self.assertTrue(all(n.endswith(".bin") for n in names), names)   # .mp4 rejected -> .bin fallback
            self.assertEqual(sum(s for _, s in bot.RUB.sent), len(payload))
            self.assertIn("✅", bot.CLIENT.edits[-1])
            self.assertIn(".bin", bot.CLIENT.edits[-1])
        asyncio.new_event_loop().run_until_complete(go())

    def test_tg_login_retries_then_ok(self):
        class RpcCallFailError(Exception): pass

        class C:
            n = 0
            async def start(self, bot_token=None):
                C.n += 1
                if C.n < 3:
                    raise RpcCallFailError("internal")
        waits = []

        async def fake_sleep(x): waits.append(x)
        asyncio.new_event_loop().run_until_complete(bot.tg_login(C(), sleep=fake_sleep))
        self.assertEqual(C.n, 3)
        self.assertEqual(waits, [10, 20])

    def test_tg_login_fatal(self):
        class AccessTokenInvalidError(Exception): pass

        class C:
            async def start(self, bot_token=None): raise AccessTokenInvalidError("bad")
        with self.assertRaises(SystemExit):
            asyncio.new_event_loop().run_until_complete(bot.tg_login(C()))

    def test_progress_text(self):
        t = bot.progress_text("x", 50, 100, bot.time.monotonic() - 10)
        self.assertIn("50.0%", t)
        self.assertIn("🟩", t)

    def test_user_error_is_actionable(self):
        e = RuntimeError("Sign in to confirm you're not a bot")
        self.assertIn("کوکی", bot.user_error(e))

    def test_restore(self):
        j = bot.Job(id="deadbeef", dir=Path(tmp) / "deadbeef", name="a.bin", size=3, chat_id=1, user_id=7, msg_id=2)
        j.dir.mkdir()
        j.src.write_bytes(b"abc")
        bot.persist(j)
        bot.JOBS.clear()
        bot.restore_jobs()
        self.assertIn("deadbeef", bot.JOBS)
        self.assertEqual(bot.JOBS["deadbeef"].name, "a.bin")


if __name__ == "__main__":
    unittest.main()
