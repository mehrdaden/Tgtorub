"""Tests for the slow-upload watchdog, parallel parts, progress rendering and /speed verdicts."""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uploader  # noqa: E402
from nettest import verdict  # noqa: E402
from uploader import StallPolicy, UploadFailed, upload_part  # noqa: E402


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def nosleep(_):
    return None


class Counter:
    """Fake /proc/net/dev: bytes grow at `rate` B/s."""
    def __init__(self, rate):
        import time
        self.rate, self.t0, self._time = rate, time.monotonic(), time

    def __call__(self):
        return int((self._time.monotonic() - self.t0) * self.rate)


class Slow:
    """First `slow_attempts` attempts hang (never finish); later ones finish fast."""
    def __init__(self, slow_attempts, finish_after=0.0):
        self.calls, self.reconnects, self.slow_attempts, self.finish_after = 0, 0, slow_attempts, finish_after

    async def reconnect(self):
        self.reconnects += 1

    async def send_file(self, path, caption):
        self.calls += 1
        if self.calls <= self.slow_attempts:
            await asyncio.sleep(3600)
        await asyncio.sleep(self.finish_after)


class Stall(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.f = Path(self.t.name) / "a.bin"
        self.f.write_bytes(os.urandom(1000))
        self._orig = uploader.tx_bytes

    def tearDown(self):
        uploader.tx_bytes = self._orig
        self.t.cleanup()

    def test_slow_upload_is_cancelled_and_retried(self):
        uploader.tx_bytes = Counter(rate=500)            # 500 B/s « 60 KB/s
        pol = StallPolicy(min_speed=60 * 1024, window=1.0, grace=0.3, max_aborts=3)
        s = Slow(slow_attempts=2)
        res = run(upload_part(s, self.f, policy=pol, poll_interval=0.1, sleep=nosleep))
        self.assertEqual(s.calls, 3)
        self.assertEqual(res.attempt, 3)
        self.assertEqual(pol.aborts, 2)
        self.assertGreaterEqual(s.reconnects, 2)         # fresh connection after every abort

    def test_fast_upload_is_not_cancelled(self):
        uploader.tx_bytes = Counter(rate=5_000_000)      # 5 MB/s
        pol = StallPolicy(min_speed=60 * 1024, window=1.0, grace=0.3, max_aborts=3)
        s = Slow(slow_attempts=0, finish_after=2.0)
        res = run(upload_part(s, self.f, policy=pol, poll_interval=0.1, sleep=nosleep))
        self.assertEqual((s.calls, res.attempt, pol.aborts), (1, 1, 0))

    def test_after_max_aborts_slow_upload_is_allowed_to_finish(self):
        uploader.tx_bytes = Counter(rate=500)
        pol = StallPolicy(min_speed=60 * 1024, window=1.0, grace=0.3, max_aborts=1)
        s = Slow(slow_attempts=1, finish_after=2.5)      # 2nd attempt is slow (500 B/s) but completes
        res = run(upload_part(s, self.f, policy=pol, poll_interval=0.1, sleep=nosleep))
        self.assertEqual((s.calls, res.attempt, pol.aborts), (2, 2, 1))

    def test_progress_callback_reports_bytes(self):
        uploader.tx_bytes = Counter(rate=100_000)
        seen = []
        s = Slow(slow_attempts=0, finish_after=1.0)
        run(upload_part(s, self.f, policy=StallPolicy(enabled=False), poll_interval=0.1,
                        on_progress=lambda sent, el: seen.append(sent), sleep=nosleep))
        self.assertTrue(seen and max(seen) > 10_000)

    def test_exhausted_stalls_raise_upload_failed(self):
        uploader.tx_bytes = Counter(rate=10)
        pol = StallPolicy(min_speed=60 * 1024, window=0.5, grace=0.1, max_aborts=99)
        s = Slow(slow_attempts=99)
        with self.assertRaises(UploadFailed):
            run(upload_part(s, self.f, attempts=3, policy=pol, poll_interval=0.05, sleep=nosleep))
        self.assertEqual(s.calls, 3)


class Target(unittest.TestCase):
    def test_falls_back_to_own_guid_when_me_is_rejected(self):
        calls = []

        class C:
            guid = "u0ABC"

            async def send_document(self, target, path, caption=None):
                calls.append(target)
                if target == "me":
                    raise RuntimeError("INVALID_INPUT object_guid")
        sess = uploader.RubikaSession("x", "me")
        sess.client = C()
        run(sess.send_file(Path("/tmp/x"), "cap"))
        self.assertEqual(calls, ["me", "u0ABC"])
        self.assertEqual(sess.target, "u0ABC")          # remembered for the next uploads


class Verdict(unittest.TestCase):
    def test_texts(self):
        self.assertIn("روبیکا خیلی کندتر", verdict(10e6, 1e5, None))
        self.assertIn("UPLOAD_STREAMS=3", verdict(10e6, 1e5, 3e5))
        self.assertIn("فایده", verdict(10e6, 1e5, 1.1e5))
        self.assertIn("داده", verdict(None, None, None))


class Flow(unittest.TestCase):
    """Parallel parts + progress text through the real run_job with fakes."""
    def test_parallel_parts_and_render(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import test_flow as tf          # installs the fake telethon / dotenv modules and imports bot
        bot = tf.bot

        async def go():
            payload = os.urandom(6_000_000)
            bot.CLIENT = tf.FakeClient(payload)
            sent = []

            class R:
                inflight = 0
                peak = 0

                async def reconnect(self): pass

                async def send_file(self, path, caption):
                    R.inflight += 1
                    R.peak = max(R.peak, R.inflight)
                    await asyncio.sleep(0.2)
                    sent.append((path.name, path.stat().st_size))
                    R.inflight -= 1
            bot.RUB = R()
            bot.UPLOAD_STREAMS = 3
            bot.UP_LOCK, bot.DL_SEM = asyncio.Lock(), asyncio.Semaphore(2)
            j = bot.Job(id="c0ffee01", dir=Path(tf.tmp) / "c0ffee01", name="a.bin", size=len(payload), chat_id=1,
                        user_id=7, msg_id=5)
            j.dir.mkdir()
            j.src.write_bytes(payload)
            j.parts = 3
            j.auto_delete = True
            bot.JOBS[j.id] = j
            bot.start_upload(j)
            await j.task
            self.assertEqual(sorted(n for n, _ in sent), ["a.bin.001", "a.bin.002", "a.bin.003"])
            self.assertEqual(sum(s for _, s in sent), len(payload))
            self.assertEqual(R.peak, 3)                      # parts really ran at the same time
            last = bot.CLIENT.edits[-1]
            self.assertIn("✅", last)
            self.assertNotIn("\\n", last)                    # no literal backslash-n in messages
            self.assertTrue(any("📤" in e for e in bot.CLIENT.edits))
        run(go())

    def test_no_literal_backslash_n_in_source(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn('\\\\n', src)


if __name__ == "__main__":
    unittest.main()
