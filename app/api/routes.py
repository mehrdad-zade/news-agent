"""
app/api/routes.py
~~~~~~~~~~~~~~~~~
All HTTP route handlers.  The FastAPI ``APIRouter`` defined here is registered
on the application in ``app/main.py``.
"""
import asyncio
import sys
from typing import Optional

from fastapi import APIRouter, Query

from app.browser.session import get_browser, get_cdp_context, get_driver_lock, get_startup_error, get_startup_mode
from app.core.config import ARTICLE_SELECTORS
from app.core.response import PrettyJSONResponse
from app.scraper.article import fetch_article_content
from app.scraper.news_list import ScrapeError, scrape_news_list

router = APIRouter()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@router.get("/")
def read_root():
    return {
        "message": "Baha News Agent API is running.",
        "endpoints": {
            "news": "/api/bahanews",
            "status": "/api/status",
            "debug": "/api/debug-page?url=<url>",
        },
    }


# ---------------------------------------------------------------------------
# Connection status & troubleshooting
# ---------------------------------------------------------------------------

@router.get("/api/status")
async def get_status():
    """Return the current browser connection state and session diagnostics.

    Use this endpoint to troubleshoot authentication problems.
    """
    browser = get_browser()
    startup_err = get_startup_error()

    if browser is None:
        return PrettyJSONResponse(
            content={
                "status": "error",
                "browser": "unavailable",
                "authenticated_session": False,
                "startup_error": startup_err,
                "troubleshooting": [
                    "1. Close all Chrome windows.",
                    "2. Launch Chrome: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome"
                    " --remote-debugging-port=9222 --no-first-run",
                    "3. Log into baha.com in that Chrome window.",
                    "4. Restart this server (uvicorn app.main:app --reload).",
                ],
            },
            status_code=503,
        )

    # Use Selenium to read cookies for baha.com
    cookie_count: int = 0
    baha_cookies: list = []
    driver = get_cdp_context()
    if driver is not None:
        try:
            def _read_cookies():
                with get_driver_lock():
                    driver.get("https://www.baha.com/")
                    return driver.get_cookies()
            all_cookies = await asyncio.to_thread(_read_cookies)
            baha_cookies = [c for c in all_cookies if "baha" in c.get("domain", "")]
            cookie_count = len(all_cookies)
        except Exception as exc:
            print(f"[status] Could not read cookies: {exc}", file=sys.stderr)

    authenticated = len(baha_cookies) > 0

    return PrettyJSONResponse(content={
        "status": "ok",
        "browser_mode": get_startup_mode() or "selenium",
        "authenticated_session": authenticated,
        "total_cookies": cookie_count,
        "baha_cookies_found": len(baha_cookies),
        "baha_cookie_names": [c["name"] for c in baha_cookies],
        "startup_warning": startup_err,
        "troubleshooting": (
            [] if authenticated else [
                "baha.com session cookies not found.",
                "Option A: Launch Chrome with --remote-debugging-port=9222 before starting the server.",
                "Option B: Make sure you are logged into baha.com in Chrome (for profile-copy mode).",
                "Then restart the server.",
            ]
        ),
    })


# ---------------------------------------------------------------------------
# Main news feed
# ---------------------------------------------------------------------------
# http://localhost:8000/api/bahanews?hours=1&search=china

@router.get("/api/bahanews")
async def get_top_news(
    hours: Optional[int] = Query(
        None, description="Return only news posted within this many hours"
    ),
    search: Optional[str] = Query(
        None, description="Filter results to items whose title or content contains this keyword (case-insensitive)"
    ),
):
    """Return the latest baha.com news with full article content.

    Query parameters
    ----------------
    hours : int, optional
        When provided, filters to items posted within the last *hours* hours.
    """
    browser = get_browser()
    if browser is None:
        startup_err = get_startup_error()
        return PrettyJSONResponse(
            content={
                "status": "error",
                "message": "Browser not available.",
                "startup_error": startup_err,
                "troubleshooting": "Visit /api/status for setup instructions.",
            },
            status_code=503,
        )

    driver = get_cdp_context()
    if driver is None:
        return PrettyJSONResponse(
            content={
                "status": "error",
                "message": "Browser driver not available.",
                "troubleshooting": "Visit /api/status for diagnostics.",
            },
            status_code=503,
        )

    try:
        # ── Phase 1: collect all URLs + titles ────────────────────────────
        try:
            news_items = await scrape_news_list(driver, max_hours=hours)
        except ScrapeError as scrape_exc:
            return PrettyJSONResponse(
                content={
                    "status": "error",
                    "message": str(scrape_exc),
                    "troubleshooting": "Visit /api/status for diagnostics.",
                },
                status_code=401,
            )

        print(f"[routes] Phase 1 done: {len(news_items)} items found", file=sys.stderr)

        # ── Phase 2: fetch full content for EVERY item ────────────────────
        BATCH = 5
        for i in range(0, len(news_items), BATCH):
            batch = news_items[i : i + BATCH]

            async def _enrich(item, _drv=driver):
                result = await fetch_article_content(_drv, item.url)
                item.content = result

            await asyncio.gather(*[_enrich(item) for item in batch])

        print(f"[routes] Phase 2 done: content fetched for all {len(news_items)} items", file=sys.stderr)

    # ── Phase 3: apply keyword filter (content only, after full fetch) ────
    if search:
        keyword = search.lower()
        before = len(news_items)
        news_items = [
            item for item in news_items
            if item.content
            and not item.content.startswith("[ERROR]")
            and not item.content.startswith("[LOGIN")
            and keyword in item.content.lower()
        ]
        print(
            f"[routes] Phase 3 search='{search}': {before} → {len(news_items)} items after filtering",
            file=sys.stderr,
        )

    # Separate clean items from errored ones for transparency
    errors = [
        {"title": item.title, "url": item.url, "error": item.content}
        for item in news_items
        if item.content and item.content.startswith("[ERROR]")
    ]

    payload = {
        "status": "success",
        "authenticated_session": True,
        "time_frame_hours": hours,
        "search": search,
        "total_found": len(news_items),
        "fetch_errors": len(errors),
        "news": [
            {k: v for k, v in item.model_dump().items() if k != "hours_ago"}
            for item in news_items
        ],
    }
    if errors:
        payload["error_details"] = errors

    return PrettyJSONResponse(content=payload)


# ---------------------------------------------------------------------------
# Debug: identify the correct CSS selector for a news detail page
# ---------------------------------------------------------------------------

@router.get("/api/debug-page")
async def debug_page(
    url: str = Query(..., description="Full URL of a baha.com news detail page"),
):
    """Return all CSS class names on the page and what each ARTICLE_SELECTORS entry extracts.

    Useful when baha.com updates its markup and selectors need refreshing.
    Also shows whether the page was loaded in an authenticated session.
    """
    browser = get_browser()
    if browser is None:
        return PrettyJSONResponse(
            content={
                "status": "error",
                "message": "Browser not available.",
                "troubleshooting": "Visit /api/status for setup instructions.",
            },
            status_code=503,
        )

    driver = get_cdp_context()
    if driver is None:
        return PrettyJSONResponse(
            content={"status": "error", "message": "Browser driver not available."},
            status_code=503,
        )

    def _sync_debug():
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException

        with get_driver_lock():
            try:
                driver.get(url)
            except Exception as nav_exc:
                return {"status": "error", "url": url, "message": f"Navigation error: {nav_exc}"}

            import time; time.sleep(2)
            final_url = driver.current_url
            redirected = final_url.rstrip("/") != url.rstrip("/")

            all_classes = driver.execute_script("""
                var cls = new Set();
                document.querySelectorAll('[class]').forEach(function(el){
                    var cn = typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '');
                    cn.split(/\\s+/).forEach(function(c){ if(c) cls.add(c); });
                });
                return Array.from(cls).sort();
            """)

            selector_hits = {}
            for sel in ARTICLE_SELECTORS:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    selector_hits[sel] = (el.text or "").strip()[:300]
                except Exception:
                    selector_hits[sel] = None

            page_text_sample = driver.execute_script(
                "return document.body ? document.body.innerText.slice(0,500) : ''"
            ) or ""
            login_detected = any(
                s in page_text_sample.lower()
                for s in ("sign in", "log in", "create an account", "subscribe")
            )

            return {
                "url": url,
                "final_url": final_url,
                "redirected": redirected,
                "authenticated_session": True,
                "login_wall_detected": login_detected,
                "all_classes": all_classes,
                "selector_hits": selector_hits,
                "page_text_sample": page_text_sample,
            }

    try:
        result = await asyncio.to_thread(_sync_debug)
        status_code = 502 if result.get("status") == "error" else 200
        return PrettyJSONResponse(content=result, status_code=status_code)
    except Exception as exc:
        return PrettyJSONResponse(
            content={"status": "error", "url": url, "message": str(exc)},
            status_code=500,
        )
