import asyncio
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import link_download as ld  # noqa: E402
from link_download import extract_url, extract_urls  # noqa: E402

DATA = os.urandom(40 * 1024 * 1024 + 123)     # > MIN_PARALLEL so the multi-connection path is used
SMALL = os.urandom(5000)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        H.hits.append((self.path, self.headers.get("Range")))
        if self.path.startswith("/page"):
            body = b"<html><body>video page</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/redir"):
            self.send_response(302)
            self.send_header("Location", "/big.zip")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = SMALL if self.path.startswith("/small") else DATA
        ctype = "application/zip"
        rng = self.headers.get("Range")
        norange = self.path.startswith("/norange")
        if rng and not norange:
            a, _, b = rng.replace("bytes=", "").partition("-")
            a = int(a)
            b = int(b) if b else len(data) - 1
            chunk = data[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, len(data)))
        else:
            chunk = data
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(chunk)))
        if not norange:
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%D9%81%D8%A7%DB%8C%D9%84.zip")
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Parsing(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(extract_url("لطفاً دانلود کن https://example.com/a.mp4?x=1."), "https://example.com/a.mp4?x=1")
        self.assertEqual(len(extract_urls("https://a.com/1 و https://b.com/2 و https://a.com/1")), 2)
        self.assertIsNone(extract_url("ftp://example.com/file"))
        self.assertIsNone(extract_url("https://user:pass@example.com/file"))
        self.assertIsNone(extract_url("http://localhost/private"))
        self.assertIsNone(extract_url("http://127.0.0.1:8000/x"))
        self.assertIsNone(extract_url("http://192.168.1.10/x"))

    def test_blocks_private_dns(self):
        with self.assertRaises(ld.LinkError):
            ld.check_public_host("127.0.0.1")

    def test_dns_failure_fails_closed(self):
        original = ld.socket.getaddrinfo
        try:
            def fail(*args, **kwargs):
                raise OSError("temporary DNS failure")
            ld.socket.getaddrinfo = fail
            with self.assertRaisesRegex(ld.LinkError, "could not verify"):
                ld.check_public_host("example.invalid")
        finally:
            ld.socket.getaddrinfo = original


class Direct(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls._orig = ld.check_public_host
        ld.check_public_host = lambda host: None        # the test server is on loopback

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        ld.check_public_host = cls._orig

    def run_dl(self, path, **kw):
        with tempfile.TemporaryDirectory() as d:
            seen = []
            res = asyncio.new_event_loop().run_until_complete(
                ld.download_url("http://127.0.0.1:%d%s" % (self.port, path), Path(d) / "x", 200 * 1024 * 1024,
                                progress=lambda c, t: seen.append((c, t)), **kw))
            return res.name, res.read_bytes(), seen

    def test_parallel_ranges(self):
        H.hits.clear()
        name, data, seen = self.run_dl("/big.zip", connections=4)
        self.assertEqual(name, "فایل.zip")
        self.assertEqual(data, DATA)
        ranged = [r for _, r in H.hits if r and r != "bytes=0-0"]
        self.assertGreaterEqual(len(ranged), 4)             # several connections really were used
        self.assertEqual(max(c for c, _ in seen), len(DATA))

    def test_no_range_server_single_stream(self):
        name, data, _ = self.run_dl("/norange.zip", connections=4)
        self.assertEqual(data, DATA)

    def test_small_file_and_redirect(self):
        name, data, _ = self.run_dl("/small.zip")
        self.assertEqual(data, SMALL)
        name, data, _ = self.run_dl("/redir")
        self.assertEqual(data, DATA)

    def test_size_limit(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ld.LinkError):
                asyncio.new_event_loop().run_until_complete(
                    ld.download_url("http://127.0.0.1:%d/big.zip" % self.port, Path(d), 1024 * 1024))

    def test_html_goes_to_ytdlp(self):
        called = {}

        async def fake(url, dest, max_bytes, progress, **kw):
            called["url"] = url
            p = dest / "v.mp4"
            dest.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
            return p
        orig = ld._ytdlp
        ld._ytdlp = fake
        try:
            self.run_dl("/page")
        finally:
            ld._ytdlp = orig
        self.assertTrue(called["url"].endswith("/page"))


class YtDlpSubprocess(unittest.TestCase):

    def test_friendly_age_gate(self):
        self.assertIn("محدودیت سنی", ld._friendly(["ERROR: This video is age-restricted"]))
        self.assertIn("کوکی", ld._friendly(["ERROR: Sign in to confirm you’re not a bot"]))

    def test_friendly_po_token(self):
        self.assertIn("PO Token", ld._friendly(["WARNING: android client requires a GVS PO Token"]))

    def test_youtube_fallback_filter(self):
        self.assertTrue(ld._is_youtube("https://youtu.be/abc"))
        self.assertTrue(ld._is_youtube("https://www.youtube.com/watch?v=abc"))
        self.assertFalse(ld._is_youtube("https://youtube.example.com/video"))
        self.assertTrue(ld._youtube_fallback_worth_trying(["HTTP Error 403: Forbidden from youtube"]))
        self.assertFalse(ld._youtube_fallback_worth_trying(["This video is age-restricted"]))

    def test_youtube_retries_once_with_web_safari(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake = root / "fake"
            (fake / "yt_dlp").mkdir(parents=True)
            (fake / "yt_dlp" / "__init__.py").write_text("")
            (fake / "yt_dlp" / "__main__.py").write_text(
                "import os, sys, pathlib\n"
                "args = sys.argv[1:]\n"
                "state = pathlib.Path(os.environ['FAKE_YTDLP_STATE'])\n"
                "out = args[args.index('--output') + 1]\n"
                "if '--extractor-args' not in args:\n"
                " print('[youtube] WARNING: GVS PO Token required', flush=True)\n"
                " print('ERROR: unable to download video data: HTTP Error 403: Forbidden', flush=True)\n"
                " state.write_text('first-failed')\n"
                " sys.exit(1)\n"
                "assert args[args.index('--extractor-args') + 1] == 'youtube:player_client=web_safari'\n"
                "print('TG2R|4|4|4', flush=True)\n"
                "p = pathlib.Path(out.replace('%(title).150B [%(id)s].%(ext)s', 'Fallback [abc].mp4'))\n"
                "p.write_bytes(b'video')\n")
            old_path = os.environ.get("PYTHONPATH")
            old_state = os.environ.get("FAKE_YTDLP_STATE")
            os.environ["PYTHONPATH"] = str(fake)
            os.environ["FAKE_YTDLP_STATE"] = str(root / "state")
            try:
                result = asyncio.new_event_loop().run_until_complete(
                    ld._ytdlp("https://youtu.be/abc", root / "out", 10**6, None,
                              proxy=None, cookies=None, height=720))
            finally:
                if old_path is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = old_path
                if old_state is None:
                    os.environ.pop("FAKE_YTDLP_STATE", None)
                else:
                    os.environ["FAKE_YTDLP_STATE"] = old_state
            self.assertEqual(result.read_bytes(), b"video")
            self.assertTrue((root / "state").exists())

    def test_estimated_total_is_marked_approximate(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake"
            (fake / "yt_dlp").mkdir(parents=True)
            (fake / "yt_dlp" / "__init__.py").write_text("")
            (fake / "yt_dlp" / "__main__.py").write_text(
                "import sys, pathlib\n"
                "args = sys.argv[1:]\n"
                "out = args[args.index('--output') + 1]\n"
                "print('TG2R|1000|4000|NA', flush=True)\n"
                "p = pathlib.Path(out.replace('%(title).150B [%(id)s].%(ext)s', 'Estimate [abc].mp4'))\n"
                "p.write_bytes(b'v' * 4000)\n")
            old = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = str(fake)
            try:
                seen = []
                asyncio.new_event_loop().run_until_complete(
                    ld._ytdlp("https://youtu.be/x", Path(d) / "out", 10**9, lambda c, t: seen.append((c, t)),
                              proxy=None, cookies=None, height=720))
            finally:
                if old is None:
                    del os.environ["PYTHONPATH"]
                else:
                    os.environ["PYTHONPATH"] = old
            self.assertEqual(seen[-1], (1000, -4000))

    def test_progress_and_result(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake"
            (fake / "yt_dlp").mkdir(parents=True)
            (fake / "yt_dlp" / "__init__.py").write_text("")
            (fake / "yt_dlp" / "__main__.py").write_text(
                "import sys, pathlib\n"
                "args = sys.argv[1:]\n"
                "out = args[args.index('--output') + 1]\n"
                "print('TG2R|1000|NA|4000', flush=True)\n"
                "print('TG2R|4000|NA|4000', flush=True)\n"
                "p = pathlib.Path(out.replace('%(title).150B [%(id)s].%(ext)s', 'Title [abc].mp4'))\n"
                "p.write_bytes(b'v' * 4000)\n")
            old = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = str(fake)
            try:
                seen = []
                res = asyncio.new_event_loop().run_until_complete(
                    ld._ytdlp("https://youtu.be/x", Path(d) / "out", 10**9, lambda c, t: seen.append((c, t)),
                              proxy=None, cookies=None, height=720))
            finally:
                if old is None:
                    del os.environ["PYTHONPATH"]
                else:
                    os.environ["PYTHONPATH"] = old
            self.assertEqual(res.name, "Title [abc].mp4")
            self.assertEqual(seen[-1], (4000, 4000))


if __name__ == "__main__":
    unittest.main()
