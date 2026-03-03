"""
app/core/config.py
~~~~~~~~~~~~~~~~~~
Centralised constants used across the application.
"""
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Browser / network
# ---------------------------------------------------------------------------

#: Resource types to abort on every Playwright page to speed up loading.
BLOCKED_RESOURCES: frozenset[str] = frozenset({"image", "font", "media", "stylesheet"})

#: CDP endpoint of the user's running Chrome instance.
CDP_URL: str = "http://localhost:9222"

# ---------------------------------------------------------------------------
# Content filtering
# ---------------------------------------------------------------------------

#: Phrases that indicate boilerplate / footer text – used to skip unwanted
#: paragraphs during article extraction.
FOOTER_PHRASES: frozenset[str] = frozenset({
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "cookie",
    "subscribe",
    "advertisement",
    "follow us",
    "contact us",
    "copyright",
    "about us",
    "disclaimer",
    # baha.com marketing boilerplate
    "baha is a leading provider",
    "real-time market data",
    "financial instruments on more than",
    "named after the founder",
    "unlock your all-in-one platform",
    "request free demo",
    "sign in with google",
})

# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

#: Regex that matches the relative / absolute timestamps baha.com uses.
TIME_PATTERN: re.Pattern = re.compile(
    r"(\d+)\s*([mhd])(?:ays?)?\s*ago"
    r"|a\s+day\s+ago|yesterday"
    r"|(\d+)\s+(January|February|March|April|May|June|July|August|September|October|November|December)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Article extraction selectors (tried top-to-bottom; first match wins)
# ---------------------------------------------------------------------------

#: Ordered list of CSS selectors for baha.com article body containers.
ARTICLE_SELECTORS: list[str] = [
    # baha-specific confirmed selector
    ".main-news-text",
    # baha-specific (update as selectors evolve; use /api/debug-page to discover)
    ".article-body",
    ".news-body",
    ".newsText",
    ".NewsText",
    ".articleText",
    ".ArticleText",
    ".newsDetail",
    ".NewsDetail",
    ".articleContent",
    ".newsContent",
    # Generic HTML5 / common CMS fallbacks
    "article",
    ".story-body",
    ".post-body",
    '[class*="article-body"]',
    '[class*="news-body"]',
    '[class*="articleBody"]',
    '[class*="newsBody"]',
    '[class*="articleContent"]',
    '[class*="newsContent"]',
    '[class*="newsDetail"]',
    '[class*="storyBody"]',
]

# ---------------------------------------------------------------------------
# Anthropic (summarisation)
# ---------------------------------------------------------------------------

import os

#: Anthropic API key – set via ANTHROPIC_API_KEY environment variable.
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

#: Model used for the /api/summary endpoint.
ANTHROPIC_SUMMARY_MODEL: str = os.environ.get("ANTHROPIC_SUMMARY_MODEL", "claude-3-haiku-20240307")

#: System prompt sent to Anthropic on every /api/summary call.
#: Override via ANTHROPIC_SYSTEM_PROMPT env var.
_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert financial and geopolitical news analyst. "
    "You will receive a collection of news articles and tweets. "
    "Your task is to produce a structured summary that groups all content "
    "by topic. For each topic:\n"
    "  1. Write a short TITLE KEYWORD in bold (e.g. **Federal Reserve Rate Decision**).\n"
    "  2. Immediately below the title, write a concise paragraph summarising "
    "all related stories and tweets under that topic, in plain English.\n"
    "Order topics by importance/impact. Do not repeat content across topics. "
    "Omit topics if there is only trivial coverage. "
    "Do not add preamble or closing remarks \u2013 output only the grouped summaries."
)
ANTHROPIC_SYSTEM_PROMPT: str = os.environ.get("ANTHROPIC_SYSTEM_PROMPT", "") or _DEFAULT_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------

#: Bot token from @BotFather.
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

#: Chat / channel ID where summaries are posted.
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

#: How often (in minutes) the scheduler posts a summary to Telegram.
#: Set to 0 or leave blank to disable.
_schedule_interval = os.environ.get("SCHEDULE_INTERVAL_MINUTES", "0")
SCHEDULE_INTERVAL_MINUTES: int = int(_schedule_interval) if _schedule_interval.isdigit() else 0

#: Only include news/tweets from the last N hours (passed to the scraper).
_schedule_hours = os.environ.get("SCHEDULE_HOURS", "")
SCHEDULE_HOURS: Optional[int] = int(_schedule_hours) if _schedule_hours.isdigit() else None

#: Keyword filter applied before summarisation (empty string = no filter).
SCHEDULE_SEARCH: Optional[str] = os.environ.get("SCHEDULE_SEARCH") or None

#: Which endpoint the scheduler uses to feed the Telegram post.
#: Accepted values: "summary" | "bahanews" | "xnews"  (default: "summary")
SCHEDULE_ENDPOINT: str = os.environ.get("SCHEDULE_ENDPOINT", "summary").lower().strip()

# ---------------------------------------------------------------------------
# X (Twitter) scraper
# ---------------------------------------------------------------------------

def _parse_twitter_handles(raw: str) -> list[str]:
    """Parse a comma-separated handle string into a cleaned list."""
    handles = []
    for h in raw.split(","):
        h = h.strip()
        if not h:
            continue
        if not h.startswith("@"):
            h = f"@{h}"
        handles.append(h)
    return handles or ["@marionawfal"]

#: X handles to scrape – comma-separated in env.  Defaults to @marionawfal.
_raw_handles = os.environ.get("TWITTER_HANDLES", "@marionawfal")
TWITTER_HANDLES: list[str] = _parse_twitter_handles(_raw_handles)

# ---------------------------------------------------------------------------
# Public URL (set by Pinggy tunnel at startup)
# ---------------------------------------------------------------------------

def _read_public_url() -> str:
    """Read the public URL written by start.sh after the Pinggy tunnel connects."""
    try:
        with open("/tmp/news_agent_public_url") as _f:
            return _f.read().strip()
    except Exception:
        return ""

#: Public base URL exposed via Pinggy (e.g. https://abc.a.pinggy.io).
PUBLIC_URL: str = os.environ.get("PUBLIC_URL", "") or _read_public_url()
