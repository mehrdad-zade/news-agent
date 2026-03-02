"""
app/scraper/article.py
~~~~~~~~~~~~~~~~~~~~~~
Fetches article body from a baha.com news detail page using Selenium.
All Selenium calls run in a thread pool (Selenium is not async-safe).
"""
from __future__ import annotations

import asyncio
import sys

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.browser.session import get_driver_lock
from app.core.config import ARTICLE_SELECTORS, FOOTER_PHRASES

_LOGIN_FRAGMENTS = ("login", "signin", "register", "subscribe", "feedback")


def _sync_fetch(driver, url: str) -> str:
    try:
        driver.get(url)

        cur = driver.current_url.lower()
        for frag in _LOGIN_FRAGMENTS:
            if frag in cur:
                return (
                    f"[LOGIN REQUIRED] Redirected to {driver.current_url}. "
                    "Make sure you are logged into baha.com in Chrome."
                )

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".main-news-text"))
            )
        except TimeoutException:
            pass

        # Pass 1: known CSS selectors
        for sel in ARTICLE_SELECTORS:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                text = el.text.strip()
                if len(text) > 100:
                    return _clean(text)
            except Exception:
                continue

        # Pass 2: JS text-density scoring
        bp = list(FOOTER_PHRASES)
        try:
            res = driver.execute_script(
                """
                var bp=arguments[0],SKIP=new Set(["SCRIPT","STYLE","NOSCRIPT","HEADER","FOOTER","NAV","ASIDE"]);
                var best=null,bestScore=0;
                document.querySelectorAll("div,section,article,main").forEach(function(el){
                    if(SKIP.has(el.tagName))return;
                    var raw=el.innerHTML||"",txt=(el.innerText||el.textContent||"").trim();
                    if(txt.length<150)return;
                    var lc=0;el.querySelectorAll("a").forEach(function(a){lc+=(a.textContent||"").length;});
                    if(lc/txt.length>0.5)return;
                    var low=txt.toLowerCase();
                    for(var i=0;i<bp.length;i++){if(low.indexOf(bp[i])>=0)return;}
                    var s=txt.length/(raw.length||1);
                    if(s>bestScore){bestScore=s;best=txt;}
                });
                return best;
                """,
                bp,
            )
            if res and len(res) > 100:
                return _clean(res)
        except Exception:
            pass

        # Pass 3: BeautifulSoup <p> fallback — restrict to article/main/section containers
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        # Only look inside content-like containers, not the whole page
        containers = soup.select(
            "article, main, [class*='article'], [class*='content'], [class*='news'], [class*='text'], [class*='body']"
        ) or [soup.body]
        seen: set = set()
        paras = []
        for container in containers:
            if container is None:
                continue
            for p in container.find_all("p"):
                t = p.get_text(separator=" ", strip=True)
                if (
                    len(t) >= 80
                    and t not in seen
                    and not any(fp in t.lower() for fp in FOOTER_PHRASES)
                ):
                    seen.add(t)
                    paras.append(t)
        if paras:
            return " ".join(paras)

        return "[ERROR] Article content not found on page."

    except WebDriverException as exc:
        return f"[ERROR] WebDriver error: {exc.msg or exc}"
    except Exception as exc:
        return f"[ERROR] {exc}"


async def fetch_article_content(driver, url: str) -> str:
    lock = get_driver_lock()
    def _locked():
        with lock:
            return _sync_fetch(driver, url)
    return await asyncio.to_thread(_locked)


def _clean(text: str) -> str:
    return " ".join(ln.strip() for ln in text.splitlines() if ln.strip())
