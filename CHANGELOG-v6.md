# tg2rub v6 changelog

Historical v5 and v5.1 changes are retained below for traceability.

- Removed destructive deletion of legacy `tgdata` and `tgtmp` directories from the update installer.
- Added timestamped backups of the currently installed application source before replacement.
- Fixed `.env` preservation for option names containing digits.
- The initial v5 Python minimum was 3.10; v5.1 raised it to 3.11 to match current yt-dlp.
- Added `yt-dlp-ejs` for YouTube JavaScript challenge solving and updated `/ytdlp_update` to update it alongside yt-dlp.
- Added clearer Persian error messages for age gates, login/cookies, unsupported URLs, HTTP 403/429, missing formats, and network timeouts.
- Redesigned progress output with a larger graphical bar, decimal percentages, throughput, ETA, and elapsed time.
- Marked estimated yt-dlp totals and Rubika upload progress as approximate instead of presenting them as exact.
- Kept Rubika upload concurrency at one by default; the PlayDL chunk uploader patch was not copied because it must be adapted and tested against the exact installed Rubpy internals first.

## Validation

- Python compile check: passed.
- Installer shell syntax check: passed.
- Unit tests: see test result supplied with this release.
- Real Telegram/Rubika uploads and live website downloads: not tested in this build environment.


## v5.1 — Python runtime safety
- Require Python 3.11 for the bot virtual environment (current yt-dlp requires Python 3.11+).
- On Ubuntu 22.04/24.04, install Python 3.11 and python3.11-venv automatically through Deadsnakes when absent.
- Build and validate a fresh virtual environment before swapping it into service.
- Keep the previous venv under /opt/tg2rub/backups; do not delete it automatically.
- Do not alter Ubuntu system Python.
- No patterniha TLS/SNI packet-injection code was embedded: it is not a safe drop-in upload accelerator for Rubpy.


## v6 (2026-11-25)
- YouTube: warnings are retained instead of being hidden; supported Deno/Node runtimes are explicitly detected and passed to yt-dlp.
- YouTube: one controlled retry using `web_safari` is made only for relevant 403/SABR/PO-token/format/challenge failures; age gates, private videos and sign-in challenges are not retried as if they were transport errors.
- yt-dlp requirement raised to the current 2026.10.26 line; EJS is supplied by the `default` extra to keep its version aligned with yt-dlp.
- Adult-site links: generic yt-dlp extractor support is documented; no DRM, paywall, private-account or access-control bypass is added.
- Patterniha/TLS/SNI: reviewed, but no raw packet injection or unverified Rubpy proxy patch is included.
- Installer now verifies Deno >=2.3 instead of accepting any old Deno silently.
- Added regression tests for YouTube fallback selection and PO Token diagnostics.

## Validation for v6 package
- Two independent test runs: 41 passed, 1 skipped, 24 subtests passed in each run.
- The skipped test requires `pyzipper`, which is declared in requirements but was not installed in the isolated validation runtime.
- `compileall`, installer `bash -n`, and ZIP integrity checks passed.
- Live YouTube/adult-site downloads, live Telegram/Rubika upload, and Ubuntu installation were not available for testing.


## v6.1 security maintenance
- Host validation now fails closed when DNS cannot be verified, instead of allowing the downloader to continue without a public-address check.
- The in-bot `/ytdlp_update` command now respects the same tested yt-dlp version range as `requirements.txt` and cannot silently jump to a future major version.
- Added a regression test for DNS-resolution failure.
- X/Twitter account login was deliberately not added: X OAuth tokens are not browser cookies and do not authenticate yt-dlp's media extractor. A fake or partial login feature would add credential/session risk without solving the download problem.
