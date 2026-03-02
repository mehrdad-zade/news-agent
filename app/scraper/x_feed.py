"""
app/scraper/x_feed.py
~~~~~~~~~~~~~~~~~~~~~
Scrapes the X (Twitter) profile feed for @MarioNawfal using Selenium.
Re-uses the shared WebDriver managed by app.browser.session.

Authentication is inherited from the Chrome profile — the same cookie-copy
mechanism used for baha.com means the user's X session is available
automatically with no extra configuration.
"""
from __future__ import annotations

import asyncio
import math
import sys
import time as _time
from datetime import datetime, timezone
from typing import List, Optional

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.browser.session import get_driver_lock
from app.models.tweet import TweetItem


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Target X profile URL.
X_PROFILE_URL = "https://x.com/MarioNawfal"
X_BASE_URL = "https://x.com"

#: URL fragments that indicate an auth redirect.
_LOGIN_URL_FRAGMENTS = ("login", "i/flow", "i/oauth")

#: Page-text signals that indicate a login wall.
_LOGIN_PAGE_SIGNALS = (
    "sign in to x",
    "sign in",
    "log in to x",
    "create account",
)

#: Worst-case tweet volume used to estimate required scroll count.
_MAX_TWEETS_PER_HOUR = 500

#: Conservative estimate of new tweets loaded per scroll step.
_TWEETS_PER_SCROLL = 15

#: Hard cap on scrolls when max_hours is not specified (open-ended request).
_SCROLLS_UNBOUNDED_CAP = 200

#: Pixels to scroll per step.
_SCROLL_STEP_PX = 3000

#: Seconds to wait after each scroll for new tweets to render.
_SCROLL_SLEEP_S = 0.8

#: When the oldest visible tweet exceeds max_hours by this multiplier, stop scrolling.
#: Using 1.1 so we only stop once we've clearly gone past the cutoff.
_SCROLL_OVERSHOOT_FACTOR = 1.1


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _parse_x_datetime(dt_str: str) -> tuple[datetime, float, str]:
    """Parse X's ISO 8601 UTC datetime attribute into (datetime, hours_ago, label).

    X emits timestamps like ``2026-03-02T14:30:00.000Z``.
    Compatible with Python 3.9 (no walrus / late fromisoformat features).
    """
    normalised = dt_str.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(normalised, fmt)
            break
        except ValueError:
            continue
    else:
        # Fallback: return a very old time so the item is never included
        return datetime.now(timezone.utc), 999.0, "unknown"

    now = datetime.now(timezone.utc)
    delta_seconds = max(0.0, (now - dt).total_seconds())
    hours_ago = delta_seconds / 3600.0

    if hours_ago < 1:
        minutes = int(delta_seconds / 60)
        label = f"{minutes}m ago" if minutes > 0 else "just now"
    elif hours_ago < 24:
        label = f"{int(hours_ago)}h ago"
    else:
        label = f"{int(hours_ago / 24)}d ago"

    return dt, hours_ago, label


# ---------------------------------------------------------------------------
# Core sync scraper
# ---------------------------------------------------------------------------

def _sync_scrape_x(driver, max_hours: Optional[float]) -> List[TweetItem]:
    """Navigate to the X profile and scrape tweets synchronously.

    Must be called inside a ``threading.Lock`` — Selenium is not thread-safe.
    """
    try:
        driver.get(X_PROFILE_URL)
    except WebDriverException as exc:
        raise RuntimeError(f"Navigation to {X_PROFILE_URL} failed: {exc.msg or exc}") from exc

    # --- Detect login redirect ------------------------------------------------
    _time.sleep(2)  # brief pause to allow JS redirect to settle
    cur_url = driver.current_url.lower()
    for frag in _LOGIN_URL_FRAGMENTS:
        if frag in cur_url:
            raise RuntimeError(
                f"X redirected to {driver.current_url} — not authenticated. "
                "Make sure you are logged into x.com in the Chrome profile used by this server."
            )

    early_text = (
        driver.execute_script(
            "return document.body ? document.body.innerText.slice(0, 500) : ''"
        )
        or ""
    ).lower()
    for sig in _LOGIN_PAGE_SIGNALS:
        if sig in early_text and len(early_text) < 600:
            raise RuntimeError(
                f"X login wall detected ('{sig}'). "
                "Log into x.com in Chrome and restart the server."
            )

    # --- Wait for first batch of tweets to appear ----------------------------
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'article[data-testid="tweet"]')
            )
        )
    except TimeoutException:
        print("[x_feed] Tweet articles not found within 15s — page may not be loaded.", file=sys.stderr)
        return []

    # --- Scroll + harvest loop -----------------------------------------------
    # X uses a virtual DOM: as you scroll down, nodes at the top are removed.
    # We must harvest tweets on EVERY scroll iteration so recent tweets (top of
    # feed) are captured before they are evicted from the DOM.
    #
    # Scroll count: at 500 tweets/hour and ~15 tweets/scroll we need
    # ceil(hours * 500 / 15 * 1.2) steps.  Cap open-ended requests.
    if max_hours is None:
        needed_scrolls = _SCROLLS_UNBOUNDED_CAP
    else:
        needed_scrolls = math.ceil(max_hours * _MAX_TWEETS_PER_HOUR / _TWEETS_PER_SCROLL * 1.2)

    print(
        f"[x_feed] Scrolling up to {needed_scrolls} times "
        f"(max_hours={max_hours}, {_MAX_TWEETS_PER_HOUR} tweets/h assumed).",
        file=sys.stderr,
    )

    items: List[TweetItem] = []
    seen_urls: set = set()

    def _harvest_visible() -> float:
        """Parse all currently visible articles; return oldest hours_ago seen."""
        oldest = 0.0
        for article in driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]'):
            try:
                # Content
                try:
                    text_el = article.find_element(By.CSS_SELECTOR, 'div[data-testid="tweetText"]')
                    content = text_el.text.strip()
                except Exception:
                    content = ""

                if not content:
                    continue

                # Timestamp
                try:
                    time_el = article.find_element(By.CSS_SELECTOR, "time[datetime]")
                    dt_attr = time_el.get_attribute("datetime") or ""
                    dt, hours_ago, time_posted = _parse_x_datetime(dt_attr)
                    posted_at = dt.isoformat()
                except Exception:
                    dt_attr, hours_ago, time_posted, posted_at = "", 999.0, "unknown", ""

                # URL
                try:
                    link_el = article.find_element(By.CSS_SELECTOR, 'a[href*="/status/"]')
                    href = link_el.get_attribute("href") or ""
                    if href.startswith("/"):
                        url = X_BASE_URL + href
                    elif href.startswith("http"):
                        url = href
                    else:
                        url = X_BASE_URL + "/" + href
                except Exception:
                    url = ""

                if not url or url in seen_urls:
                    if hours_ago > oldest:
                        oldest = hours_ago
                    continue

                # Only collect tweets within the requested window
                if max_hours is None or hours_ago <= max_hours:
                    seen_urls.add(url)
                    items.append(TweetItem(
                        url=url,
                        posted_at=posted_at,
                        time_posted=time_posted,
                        content=content,
                        hours_ago=hours_ago,
                    ))

                if hours_ago > oldest:
                    oldest = hours_ago

            except Exception as exc:
                print(f"[x_feed] Skipping article due to parse error: {exc}", file=sys.stderr)

        return oldest

    # Harvest the initial page load before any scrolling
    _harvest_visible()

    for scroll_idx in range(needed_scrolls):
        driver.execute_script(f"window.scrollBy(0, {_SCROLL_STEP_PX});")
        _time.sleep(_SCROLL_SLEEP_S)

        oldest_hours = _harvest_visible()

        # Early-exit once we've clearly scrolled past the requested window
        if max_hours is not None and oldest_hours > max_hours * _SCROLL_OVERSHOOT_FACTOR:
            print(
                f"[x_feed] Oldest tweet is {oldest_hours:.1f}h ago — "
                f"stopping scroll at step {scroll_idx + 1}.",
                file=sys.stderr,
            )
            break

    print(f"[x_feed] Collected {len(items)} unique tweets across all scroll steps.", file=sys.stderr)

    # Most recent first
    items.sort(key=lambda t: t.hours_ago)
    print(f"[x_feed] Returning {len(items)} tweets.", file=sys.stderr)
    return items


# ---------------------------------------------------------------------------
# Public async interface
# ---------------------------------------------------------------------------

async def scrape_x_feed(driver, max_hours: Optional[float] = None) -> List[TweetItem]:
    """Scrape the X profile feed.  Thread-safe async wrapper around ``_sync_scrape_x``."""
    lock = get_driver_lock()

    def _locked():
        with lock:
            return _sync_scrape_x(driver, max_hours)

    return await asyncio.to_thread(_locked)
