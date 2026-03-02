"""
app/scraper/news_list.py
~~~~~~~~~~~~~~~~~~~~~~~~
Scrapes the baha.com homepage for news stubs using Selenium.
"""
from __future__ import annotations

import asyncio
import sys
from typing import List, Optional

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.browser.session import get_driver_lock
from app.core.config import BLOCKED_RESOURCES, TIME_PATTERN
from app.models.news import NewsItem


class ScrapeError(RuntimeError):
    pass


BAHA_HOME = "https://www.baha.com/"
NEWS_HREF_KEYWORD = "news/details"
_BAD_URL = ("login", "signin", "register", "subscribe")
_LOGIN_SIGNALS = ("sign in", "log in", "create an account", "register now", "subscription required")


def parse_time(text: str):
    m = TIME_PATTERN.search(text)
    if not m:
        return 999.0, "unknown"
    if m.group(1) and m.group(2):
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "d":
            return float(val * 24), f"{val}d ago"
        if unit == "h":
            return float(val), f"{val}h ago"
        if unit == "m":
            return val / 60.0, f"{val}m ago"
    lower = text.lower()
    if "a day ago" in lower or "yesterday" in lower:
        return 24.0, "1d ago"
    if m.group(3) and m.group(4):
        return 48.0, f"{m.group(3)} {m.group(4)}"
    return 999.0, "unknown"


def closest_time(anchor_tag):
    node = anchor_tag.parent
    for _ in range(5):
        if node is None or not hasattr(node, "get_text"):
            break
        snippet = node.get_text(separator=" ", strip=True)[:300]
        hours, label = parse_time(snippet)
        if label != "unknown":
            return hours, label
        node = getattr(node, "parent", None)
    return 999.0, "unknown"


def _sync_scrape(driver, max_hours: Optional[float]) -> List[NewsItem]:
    try:
        driver.get(BAHA_HOME)

        cur = driver.current_url.lower()
        for frag in _BAD_URL:
            if frag in cur:
                raise ScrapeError(
                    f"Homepage redirected to {driver.current_url} — "
                    "not authenticated. Log into baha.com in Chrome and restart."
                )

        try:
            early = driver.execute_script(
                "return document.body ? document.body.innerText.slice(0,800) : \'\'"
            ) or ""
            low = early.lower()
            for sig in _LOGIN_SIGNALS:
                if sig in low and len(early) < 1000:
                    raise ScrapeError(f"Login wall detected (\'{sig}\'). Log into baha.com in Chrome.")
        except ScrapeError:
            raise
        except Exception:
            pass

        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f"a[href*=\'{NEWS_HREF_KEYWORD}\']"))
            )
        except TimeoutException:
            print("[news_list] Article links not found within 8 s.", file=sys.stderr)

        html = driver.page_source

    except ScrapeError:
        raise
    except Exception as exc:
        print(f"[news_list] Error: {exc}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen: set = set()
    items: List[NewsItem] = []

    for a in soup.find_all("a", href=lambda h: h and NEWS_HREF_KEYWORD in h):
        href: str = a["href"]
        title: str = a.get_text(strip=True)
        if not title:
            h = a.find(["h1","h2","h3","h4","span","div"])
            if h:
                title = h.get_text(strip=True)
        if not title or len(title) <= 10 or href in seen:
            continue
        if href.startswith("/"):
            href = "https://www.baha.com" + href
        elif not href.startswith("http"):
            href = "https://www.baha.com/" + href
        hours_ago, time_posted = closest_time(a)
        if max_hours is not None and hours_ago > max_hours:
            continue
        items.append(NewsItem(
            title=title.replace("\n", " ").strip(),
            url=href,
            time_posted=time_posted,
            hours_ago=hours_ago,
        ))
        seen.add(href)

    items.sort(key=lambda x: x.hours_ago)
    return items


async def scrape_news_list(driver, max_hours: Optional[float] = None) -> List[NewsItem]:
    lock = get_driver_lock()
    def _locked():
        with lock:
            return _sync_scrape(driver, max_hours)
    return await asyncio.to_thread(_locked)
