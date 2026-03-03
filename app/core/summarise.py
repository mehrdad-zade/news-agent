"""
app/core/summarise.py
~~~~~~~~~~~~~~~~~~~~~
Shared summary-building logic used by both the /api/summary HTTP route
and the background scheduler.  Keeping it here avoids duplicating the
scraping + Anthropic call in two places.
"""
import asyncio
import sys
from typing import Optional

import anthropic

from app.core.config import ANTHROPIC_API_KEY, ANTHROPIC_SUMMARY_MODEL, ANTHROPIC_SYSTEM_PROMPT, TWITTER_HANDLES
from app.scraper.article import fetch_article_content
from app.scraper.news_list import ScrapeError, scrape_news_list
from app.scraper.x_feed import scrape_x_feed


def _filter(items, search: Optional[str]) -> list:
    if not search:
        return items
    kw = search.lower()
    return [
        i for i in items
        if i.content
        and not i.content.startswith("[ERROR]")
        and not i.content.startswith("[LOGIN")
        and kw in i.content.lower()
    ]


async def build_summary(
    driver,
    hours: Optional[int] = None,
    search: Optional[str] = None,
) -> dict:
    """Scrape baha.com + X feed **sequentially** (shared driver), then ask
    Anthropic to group everything into topic-based summaries.

    Returns a dict with keys:
        status        – "success" | "error"
        summary       – formatted text (Markdown)
        news_count    – int
        tweet_count   – int
        total_sources – int
        error         – str (only when status == "error")
    """
    news_items: list = []
    tweets: list = []

    # ------------------------------------------------------------------ #
    # Scrape baha.com news                                                 #
    # ------------------------------------------------------------------ #
    try:
        print("[summary] scraping baha.com news...", file=sys.stderr)
        items = await scrape_news_list(driver, max_hours=hours)
        BATCH = 5
        for i in range(0, len(items), BATCH):
            batch = items[i : i + BATCH]

            async def _enrich(item, _drv=driver):
                item.content = await fetch_article_content(_drv, item.url)

            await asyncio.gather(*[_enrich(item) for item in batch])
        news_items = items
        print(f"[summary] baha.com: {len(news_items)} items", file=sys.stderr)
    except ScrapeError as exc:
        print(f"[summary] baha scrape failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # Scrape X feed – all configured handles                               #
    # ------------------------------------------------------------------ #
    try:
        print(f"[summary] scraping X feed for handles: {TWITTER_HANDLES}", file=sys.stderr)
        for handle in TWITTER_HANDLES:
            try:
                handle_tweets = await scrape_x_feed(driver, max_hours=hours, handle=handle)
                tweets.extend(handle_tweets)
                print(f"[summary] X {handle}: {len(handle_tweets)} tweets", file=sys.stderr)
            except RuntimeError as exc:
                print(f"[summary] X scrape failed for {handle}: {exc}", file=sys.stderr)
        print(f"[summary] X total: {len(tweets)} tweets across all handles", file=sys.stderr)
    except Exception as exc:
        print(f"[summary] X scrape error: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # Optional keyword filter                                              #
    # ------------------------------------------------------------------ #
    news_items = _filter(news_items, search)
    tweets     = _filter(tweets,     search)

    # ------------------------------------------------------------------ #
    # Build corpus                                                         #
    # ------------------------------------------------------------------ #
    corpus_parts: list[str] = []
    for item in news_items:
        if item.content and not item.content.startswith("[ERROR]") and not item.content.startswith("[LOGIN"):
            corpus_parts.append(f"[NEWS] {item.title}\n{item.content}")
    for tweet in tweets:
        if tweet.content and not tweet.content.startswith("[ERROR]") and not tweet.content.startswith("[LOGIN"):
            corpus_parts.append(f"[TWEET] {tweet.content}")

    if not corpus_parts:
        return {
            "status": "success",
            "summary": "No content found for the given parameters.",
            "news_count": len(news_items),
            "tweet_count": len(tweets),
            "total_sources": 0,
        }

    corpus = "\n\n---\n\n".join(corpus_parts)

    # ------------------------------------------------------------------ #
    # Anthropic summarisation                                              #
    # ------------------------------------------------------------------ #
    system_prompt = ANTHROPIC_SYSTEM_PROMPT
    user_message = (
        f"Summarise the following {len(corpus_parts)} items "
        f"({'news articles and tweets' if news_items and tweets else 'news articles' if news_items else 'tweets'}) "
        f"into grouped topic summaries:\n\n{corpus}"
    )

    try:
        print(f"[summary] calling Anthropic model={ANTHROPIC_SUMMARY_MODEL}", file=sys.stderr)
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=ANTHROPIC_SUMMARY_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        summary_text = message.content[0].text if message.content else ""
    except anthropic.APIError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "news_count": len(news_items),
            "tweet_count": len(tweets),
            "total_sources": len(corpus_parts),
        }

    return {
        "status": "success",
        "summary": summary_text,
        "news_count": len(news_items),
        "tweet_count": len(tweets),
        "total_sources": len(corpus_parts),
    }
