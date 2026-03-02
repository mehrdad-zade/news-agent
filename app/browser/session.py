"""
app/browser/session.py
~~~~~~~~~~~~~~~~~~~~~~
Manages a single Selenium WebDriver shared across the application.

Start-up strategy
-----------------
1. Attach to an already-running Chrome via CDP on localhost:9222.
   Launch Chrome once with:

       /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
           --remote-debugging-port=9222

2. If that fails, start a fresh headless Chrome via undetected-chromedriver
   with only the Cookies file copied from your real Chrome profile.
   Chrome decrypts its own cookies (same machine = same Keychain key),
   so baha.com session cookies are available automatically.

No Keychain code, no manual decryption anywhere in this module.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

_CHROME_PROFILE = (
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
)

_driver = None
_driver_lock = threading.Lock()
_tmp_dir: Path | None = None
_startup_error: str | None = None
_startup_mode: str | None = None  # "cdp" or "headless"


def get_driver():
    return _driver

def get_browser():
    return _driver

def get_cdp_context():
    return _driver

def get_driver_lock() -> threading.Lock:
    return _driver_lock

def get_startup_mode() -> str | None:
    return _startup_mode

def get_startup_error() -> str | None:
    return _startup_error


def _chrome_major_version() -> int | None:
    """Detect the installed Chrome major version number."""
    import subprocess
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    try:
        out = subprocess.check_output([chrome_bin, "--version"], text=True, timeout=5).strip()
        # e.g. "Google Chrome 145.0.7632.117"
        return int(out.split()[-1].split(".")[0])
    except Exception:
        return None


def _start_driver_sync():
    global _tmp_dir, _startup_mode

    # Strategy 1: attach to existing Chrome via CDP (Selenium 4 API)
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.debugger_address = "localhost:9222"   # Selenium 4 way
        d = webdriver.Chrome(options=opts)
        _ = d.current_url
        print("[session] Attached to Chrome via CDP (localhost:9222).", file=sys.stderr)
        _startup_mode = "cdp"
        return d
    except Exception as e:
        print(f"[session] CDP attach failed: {e!s:.80} — trying profile-copy launch...", file=sys.stderr)

    # Strategy 2: headless Chrome with copied Cookies file
    src = _CHROME_PROFILE / "Default" / "Cookies"
    if not src.exists():
        raise RuntimeError(
            f"Chrome cookie database not found at {src}. "
            "Log into baha.com in Chrome at least once, then restart."
        )

    tmp = Path(tempfile.mkdtemp(prefix="news_agent_chrome_"))
    (tmp / "Default").mkdir()
    shutil.copy2(str(src), str(tmp / "Default" / "Cookies"))
    _tmp_dir = tmp

    import undetected_chromedriver as uc
    opts = uc.ChromeOptions()
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-notifications")

    # Pin ChromeDriver to the installed Chrome version to avoid version mismatch
    ver = _chrome_major_version()
    uc_kwargs = dict(user_data_dir=str(tmp), options=opts, headless=True)
    if ver:
        uc_kwargs["version_main"] = ver
        print(f"[session] Detected Chrome {ver}, using matching ChromeDriver.", file=sys.stderr)

    d = uc.Chrome(**uc_kwargs)
    print(f"[session] Headless Chrome started with profile copy: {tmp}", file=sys.stderr)
    _startup_mode = "headless"
    return d


def _stop_driver_sync():
    global _driver, _tmp_dir
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None
    if _tmp_dir is not None:
        shutil.rmtree(_tmp_dir, ignore_errors=True)
        _tmp_dir = None


@asynccontextmanager
async def browser_lifespan():
    global _driver, _startup_error, _startup_mode
    _startup_error = None
    _startup_mode = None
    try:
        _driver = await asyncio.to_thread(_start_driver_sync)
        print("[session] Browser ready.", file=sys.stderr)
    except Exception as exc:
        _startup_error = str(exc)
        print(f"[session] Could not start browser: {_startup_error}", file=sys.stderr)
    yield
    await asyncio.to_thread(_stop_driver_sync)
    print("[session] Browser shut down.", file=sys.stderr)
