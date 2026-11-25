import asyncio
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from download import parallel_download  # noqa: E402
from fileops import ascii_name, is_video_name, make_zip, safe_name, split_file  # noqa: E402
from pipeline import prepare_outputs  # noqa: E402
from uploader import FatalUploadError, UploadFailed, make_plan, upload_part  # noqa: E402

try:
    import pyzipper
except ImportError:
    pyzipper = None


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FileOps(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name)
        self.payload = os.urandom(3_000_017)
        self.src = self.r / "src.bin"
        self.src.write_bytes(self.payload)

    def tearDown(self):
        self.t.cleanup()

    def test_names(self):
        self.assertEqual(ascii_name("فیلم من.MP4"), "file.MP4")
        self.assertEqual(ascii_name("my movie (1).mkv"), "my_movie_1.mkv")
        self.assertTrue(is_video_name("a.MKV"))
        self.assertEqual(safe_name("../../etc/passwd"), "passwd")

    def test_pipeline_variants(self):
        # plain
        out = self.r / "o1"
        f = prepare_outputs(self.src, "a.mp4", out, zip_enabled=False, password=None, parts=1, max_part=10**9, zip_level=0)
        self.assertEqual([p.name for p in f], ["a.mp4"])
        # auto split by max_part
        out = self.r / "o2"
        f = prepare_outputs(self.src, "a.mp4", out, zip_enabled=False, password=None, parts=1, max_part=1_000_000, zip_level=0)
        self.assertEqual(len(f), 4)
        self.assertEqual(b"".join(p.read_bytes() for p in f), self.payload)
        # zip + 2 parts
        out = self.r / "o3"
        f = prepare_outputs(self.src, "a.mp4", out, zip_enabled=True, password=None, parts=2, max_part=10**9, zip_level=0)
        self.assertEqual([p.name for p in f], ["a.mp4.zip.001", "a.mp4.zip.002"])
        joined = self.r / "joined.zip"
        joined.write_bytes(b"".join(p.read_bytes() for p in f))
        self.assertEqual(zipfile.ZipFile(joined).read("a.mp4"), self.payload)

    @unittest.skipIf(pyzipper is None, "pyzipper missing")
    def test_aes(self):
        dst = self.r / "e.zip"
        make_zip(self.src, "a.bin", dst, 0, "password123")
        with pyzipper.AESZipFile(dst) as z:
            z.setpassword(b"password123")
            self.assertEqual(z.read("a.bin"), self.payload)


class Plan(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(len(make_plan(10)), 10)
        self.assertEqual(make_plan(10)[:3], ["orig", "orig", "ascii"])
        self.assertEqual(make_plan(10, "zip"), ["zip"] * 10)
        self.assertEqual(make_plan(10, "bin")[:5], ["bin"] * 4 + ["zip"])
        self.assertEqual(make_plan(12)[-1], "zip")
        self.assertEqual(make_plan(2), ["orig", "orig"])


class FakeSender:
    def __init__(self, fail_kinds=(), fail_times=0, exc=None):
        self.calls, self.reconnects = [], 0
        self.fail_kinds, self.fail_times, self.exc = set(fail_kinds), fail_times, exc

    async def reconnect(self):
        self.reconnects += 1

    async def send_file(self, path, caption):
        self.calls.append((path.name, caption))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise (self.exc or RuntimeError("SERVER_ERROR"))
        if path.suffix.lower() in self.fail_kinds or (self.fail_kinds and "mp4" in self.fail_kinds and path.name.endswith(".mp4")):
            raise RuntimeError("ERROR_TRY_AGAIN")


async def nosleep(_):
    return None


class Upload(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name)
        self.f = self.r / "فیلم.mp4"
        self.f.write_bytes(os.urandom(200_000))

    def tearDown(self):
        self.t.cleanup()

    def test_first_try_ok(self):
        s = FakeSender()
        res = run(upload_part(s, self.f, sleep=nosleep))
        self.assertEqual((res.kind, res.attempt), ("orig", 1))

    def test_retry_then_ok(self):
        s = FakeSender(fail_times=3)
        res = run(upload_part(s, self.f, sleep=nosleep))
        self.assertEqual(res.attempt, 4)
        self.assertEqual(res.kind, "ascii")
        self.assertGreaterEqual(s.reconnects, 1)
        self.assertEqual(s.calls[2][0], "file.mp4")       # ascii variant name

    def test_video_rejected_falls_back_to_bin(self):
        s = FakeSender(fail_kinds={".mp4"})
        res = run(upload_part(s, self.f, sleep=nosleep))
        self.assertEqual(res.kind, "bin")
        self.assertEqual(res.name, "file.mp4.bin")
        self.assertEqual(res.attempt, 6)

    def test_everything_but_zip_rejected(self):
        s = FakeSender(fail_kinds={".mp4", ".bin"})
        res = run(upload_part(s, self.f, sleep=nosleep))
        self.assertEqual(res.kind, "zip")
        self.assertEqual(res.attempt, 8)
        # the variant zip really contains the original bytes
        # (it is deleted after the call, so just check the sequence of names)
        self.assertEqual(s.calls[-1][0], "file.zip")

    def test_ten_attempts_then_failure(self):
        s = FakeSender(fail_times=999)
        with self.assertRaises(UploadFailed) as cm:
            run(upload_part(s, self.f, sleep=nosleep))
        self.assertEqual(len(s.calls), 10)
        self.assertEqual(cm.exception.attempts, 10)
        self.assertFalse(list(self.r.glob("v_*")))         # variants cleaned up

    def test_fatal_not_retried(self):
        s = FakeSender(fail_times=999, exc=RuntimeError("INVALID_AUTH"))
        with self.assertRaises(RuntimeError):
            run(upload_part(s, self.f, sleep=nosleep))
        self.assertEqual(len(s.calls), 1)

    def test_timeout_forces_reconnect(self):
        s = FakeSender(fail_times=1, exc=asyncio.TimeoutError())
        res = run(upload_part(s, self.f, sleep=nosleep))
        self.assertEqual(res.attempt, 2)
        self.assertEqual(s.reconnects, 1)

    def test_zip_variant_content(self):
        captured = {}

        class S(FakeSender):
            async def send_file(self, path, caption):
                if path.suffix == ".zip":
                    captured["data"] = zipfile.ZipFile(path).read("فیلم.mp4")
                    return
                raise RuntimeError("SERVER_ERROR")
        res = run(upload_part(S(), self.f, mode="zip", sleep=nosleep))
        self.assertEqual(res.kind, "zip")
        self.assertEqual(captured["data"], self.f.read_bytes())


class FakeClient:
    def __init__(self, data):
        self.data = data

    async def iter_download(self, media, offset=0, limit=None, request_size=512 * 1024, file_size=None):
        assert offset % request_size == 0
        pos, n = offset, 0
        while pos < len(self.data) and (limit is None or n < limit):
            await asyncio.sleep(0)
            yield self.data[pos:pos + request_size]
            pos += request_size
            n += 1


class Msg:
    media = object()


class Download(unittest.TestCase):
    def test_parallel_exact(self):
        for size in (1, 524287, 524288, 524289, 5_000_001, 20_000_000):
            for workers in (1, 3, 4, 8):
                with self.subTest(size=size, workers=workers):
                    data = os.urandom(size)
                    with tempfile.TemporaryDirectory() as d:
                        dest = Path(d) / "x"
                        seen = []
                        run(parallel_download(FakeClient(data), Msg(), dest, size, workers=workers,
                                              progress=lambda c, t: seen.append(c)))
                        self.assertEqual(dest.read_bytes(), data)
                        self.assertEqual(max(seen), size)

    def test_short_segment_detected(self):
        data = os.urandom(3_000_000)

        class Short(FakeClient):
            async def iter_download(self, media, offset=0, limit=None, request_size=512 * 1024, file_size=None):
                async for c in super().iter_download(media, offset, 1, request_size, file_size):
                    yield c
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(IOError):
                run(parallel_download(Short(data), Msg(), Path(d) / "x", len(data), workers=2))


if __name__ == "__main__":
    unittest.main()
