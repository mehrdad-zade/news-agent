"""
app/api/routes.py
~~~~~~~~~~~~~~~~~
All HTTP route handlers.  The FastAPI ``APIRouter`` defined here is registered
on the application in ``app/main.py``.
"""
import asyncio
import sys
from typing import Any, Optional, Tuple

from fastapi import APIRouter, Query

from app.browser.session import (
    get_browser,
    get_cdp_context,
    get_driver_lock,
    get_startup_error,
    get_startup_mode,
)
from app.core.config import ARTICLE_SELECTORS, ANTHROPIC_API_KEY, PUBLIC_URL, TWITTER_HANDLES
from app.core.response import PrettyJSONResponse
from app.core.summarise import build_summary
from app.scraper.article import fetch_article_content
from app.scraper.news_list import ScrapeError, scrape_news_list
from app.scraper.x_feed import scrape_x_feed

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_driver() -> Tuple[Any, Optional[PrettyJSONResponse]]:
    """Return ``(driver, None)`` when the browser is ready, or
    ``(None, error_response)`` when it is not."""
    browser = get_browser()
    if browser is None:
        startup_err = get_startup_error()
        return None, PrettyJSONResponse(
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
        return None, PrettyJSONResponse(
            content={
                "status": "error",
                "message": "Browser driver not available.",
                "troubleshooting": "Visit /api/status for diagnostics.",
            },
            status_code=503,
        )
    return driver, None


def _apply_content_filter(items, search: Optional[str]):
    """Filter items to those whose content contains search (case-insensitive).
    Items with error/login sentinel strings are excluded when a keyword is active.
    """
    if not search:
        return items
    keyword = search.lower()
    before = len(items)
    filtered = [
        item for item in items
        if item.content
        and not item.content.startswith("[ERROR]")
        and not item.content.startswith("[LOGIN")
        and keyword in item.content.lower()
    ]
    print(
        f"[routes] search='{search}': {before} -> {len(filtered)} items after filtering",
        file=sys.stderr,
    )
    return filtered


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@router.get("/", include_in_schema=False)
def read_root():
    base = PUBLIC_URL or "http://localhost:8000"
    return {
        "message": "News Agent API is running.",
        "public_url": PUBLIC_URL or None,
        "docs": {
            "swagger_ui": f"{base}/docs",
            "redoc":      f"{base}/redoc",
            "openapi":    f"{base}/openapi.json",
        },
        "endpoints": {
            "baha_news": f"{base}/api/bahanews?hours=<n>&search=<keyword>",
            "x_feed":    f"{base}/api/xnews?hours=<n>&search=<keyword>",
            "summary":   f"{base}/api/summary?hours=<n>&search=<keyword>",
            "status":    f"{base}/api/status",
            "debug":     f"{base}/api/debug-page?url=<url>",
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
# baha.com news feed
# ---------------------------------------------------------------------------

@router.get("/api/bahanews")
async def get_baha_news(
    hours: Optional[int] = Query(None, description="Return only news from the last N hours"),
    search: Optional[str] = Query(None, description="Keyword to match against article content (case-insensitive)"),
):
    """Return the latest baha.com news with full article content."""
    driver, err = _get_driver()
    if err:
        return err

    # Phase 1: collect news stubs (title + URL + timestamp)
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

    print(f"[routes] bahanews Phase 1: {len(news_items)} items", file=sys.stderr)

    # Phase 2: fetch full article content for every stub
    BATCH = 5
    for i in range(0, len(news_items), BATCH):
        batch = news_items[i : i + BATCH]

        async def _enrich(item, _drv=driver):
            item.content = await fetch_article_content(_drv, item.url)

        await asyncio.gather(*[_enrich(item) for item in batch])

    print(f"[routes] bahanews Phase 2: content fetched for {len(news_items)} items", file=sys.stderr)

    # Phase 3: optional keyword filter on content
    news_items = _apply_content_filter(news_items, search)

    errors = [
        {"title": item.title, "url": item.url, "error": item.content}
        for item in news_items
        if item.content and item.content.startswith("[ERROR]")
    ]

    payload = {
        "status": "success",
        "public_url": PUBLIC_URL or None,
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
# X (Twitter) feed – multi-handle, grouped by handle
# ---------------------------------------------------------------------------

@router.get("/api/xnews")
async def get_x_news(
    hours: Optional[int] = Query(None, description="Return only tweets from the last N hours"),
    search: Optional[str] = Query(None, description="Keyword to match against tweet content (case-insensitive)"),
):
    """Return the latest tweets from all handles in TWITTER_HANDLES.

    Results are grouped by handle, ordered newest-first within each group.
    Authentication is inherited from the Chrome profile cookie store.
    """
    driver, err = _get_driver()
    if err:
        return err

    async def _fetch_one(handle: str) -> dict:
        try:
            tweets = await scrape_x_feed(driver, max_hours=hours, handle=handle)
        except RuntimeError as exc:
            return {
                "handle": handle,
                "error": str(exc),
                "total_found": 0,
                "tweets": [],
            }

        print(f"[routes] xnews {handle}: {len(tweets)} tweets", file=sys.stderr)
        filtered = _apply_content_filter(tweets, search)

        return {
            "handle": handle,
            "total_found": len(filtered),
            "tweets": [
                {k: v for k, v in tweet.model_dump().items() if k != "hours_ago"}
                for tweet in filtered
            ],
        }

    # Scrape all handles; Selenium lock inside scrape_x_feed serialises them
    results = await asyncio.gather(*[_fetch_one(h) for h in TWITTER_HANDLES])

    total = sum(r["total_found"] for r in results)
    return PrettyJSONResponse(content={
        "status": "success",
        "public_url": PUBLIC_URL or None,
        "authenticated_session": True,
        "time_frame_hours": hours,
        "search": search,
        "handles": TWITTER_HANDLES,
        "total_found": total,
        "results": list(results),
    })


# ---------------------------------------------------------------------------
# Combined summary (OpenAI-powered semantic grouping)
# ---------------------------------------------------------------------------

@router.get("/api/summary")
async def get_summary(
    hours: Optional[int] = Query(None, description="Only include news/tweets from the last N hours"),
    search: Optional[str] = Query(None, description="Keyword filter applied before summarisation"),
):
    """Fetch baha.com news and X tweets, then return an AI-generated summary.

    All content is grouped into thematic topics by OpenAI.  Each topic gets a
    short title keyword followed by a concise plain-English summary of every
    related story/tweet found.  Requires the ``OPENAI_API_KEY`` environment
    variable to be set.
    """
    if not ANTHROPIC_API_KEY:
        return PrettyJSONResponse(
            content={
                "status": "error",
                "message": "ANTHROPIC_API_KEY is not configured on the server.",
                "troubleshooting": "Set the ANTHROPIC_API_KEY environment variable and restart the server.",
            },
            status_code=503,
        )

    driver, err = _get_driver()
    if err:
        return err

    result = await build_summary(driver, hours=hours, search=search)

    if result.get("status") == "error":
        return PrettyJSONResponse(
            content={"status": "error", "message": result.get("error", "unknown error")},
            status_code=502,
        )

    return PrettyJSONResponse(content={
        "status": "success",
        "public_url": PUBLIC_URL or None,
        "time_frame_hours": hours,
        "search": search,
        "total_sources": result["total_sources"],
        "news_count": result["news_count"],
        "tweet_count": result["tweet_count"],
        "summary": result["summary"],
    })


# ---------------------------------------------------------------------------
# Debug: identify the correct CSS selector for a news detail page
# ---------------------------------------------------------------------------

@router.get("/api/debug-page")
async def debug_page(
    url: str = Query(..., description="Full URL of a baha.com news detail page"),
):
    """Return all CSS class names on the page and what each ARTICLE_SELECTORS entry extracts.

    Useful when baha.com updates its markup and selectors need refreshing.
    """
    driver, err = _get_driver()
    if err:
        return err

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
