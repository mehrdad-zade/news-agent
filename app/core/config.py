"""
app/core/config.py
~~~~~~~~~~~~~~~~~~
Centralised constants used across the application.
"""
import re

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
