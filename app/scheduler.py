"""
app/scheduler.py
~~~~~~~~~~~~~~~~
Background asyncio task that periodically builds a news summary and
posts it to a Telegram chat.

Activated when all three env vars are set:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    SCHEDULE_INTERVAL_MINUTES   (> 0)

Optional tuning (passed through to the summary builder):
    SCHEDULE_HOURS     – only include news from last N hours
    SCHEDULE_SEARCH    – keyword filter before summarisation
"""
import asyncio
import sys

from app.core.config import (
    PUBLIC_URL,
    SCHEDULE_ENDPOINT,
    SCHEDULE_HOURS,
    SCHEDULE_INTERVAL_MINUTES,
    SCHEDULE_SEARCH,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TWITTER_HANDLES,
)
from app.telegram import send_message


async def _run_once() -> None:
    """Scrape and post one update to Telegram.

    The endpoint used is controlled by the SCHEDULE_ENDPOINT env var:
        summary   – AI-generated combined summary (default)
        bahanews  – raw baha.com news list
        xnews     – raw X / Twitter feed
    """
    # Import here to avoid circular imports at module load time
    from app.browser.session import get_cdp_context

    driver = get_cdp_context()
    if driver is None:
        print("[scheduler] browser driver not ready – skipping run.", file=sys.stderr)
        return

    print(
        f"[scheduler] running endpoint={SCHEDULE_ENDPOINT!r} "
        f"(hours={SCHEDULE_HOURS}, search={SCHEDULE_SEARCH!r})",
        file=sys.stderr,
    )

    label_parts = []
    if SCHEDULE_HOURS:
        label_parts.append(f"last {SCHEDULE_HOURS}h")
    if SCHEDULE_SEARCH:
        label_parts.append(f"`{SCHEDULE_SEARCH}`")
    label = " · ".join(label_parts)

    # ------------------------------------------------------------------
    # Branch: summary
    # ------------------------------------------------------------------
    if SCHEDULE_ENDPOINT == "summary":
        from app.core.summarise import build_summary

        result = await build_summary(driver, hours=SCHEDULE_HOURS, search=SCHEDULE_SEARCH)

        if result.get("status") == "error":
            msg = f"⚠️ *News Agent error*\n{result.get('error', 'unknown error')}"
        else:
            xnews_url = ""
            if PUBLIC_URL:
                xnews_link = f"{PUBLIC_URL}/api/xnews"
                if SCHEDULE_HOURS:
                    xnews_link += f"?hours={SCHEDULE_HOURS}"
                if SCHEDULE_SEARCH:
                    sep = "&" if SCHEDULE_HOURS else "?"
                    xnews_link += f"{sep}search={SCHEDULE_SEARCH}"
                xnews_url = f"\n\n🔗 [Live X feed]({xnews_link})"

            header = (
                f"📰 *News Summary*"
                + (f" – {label}" if label else "")
                + f"\n_"
                + f"{result['total_sources']} sources: "
                + f"{result['news_count']} articles, "
                + f"{result['tweet_count']} tweets"
                + "_\n\n"
            )
            msg = header + result.get("summary", "No summary generated.") + xnews_url

    # ------------------------------------------------------------------
    # Branch: bahanews
    # ------------------------------------------------------------------
    elif SCHEDULE_ENDPOINT == "bahanews":
        import asyncio as _asyncio
        from app.scraper.article import fetch_article_content
        from app.scraper.news_list import ScrapeError, scrape_news_list

        try:
            items = await scrape_news_list(driver, max_hours=SCHEDULE_HOURS)
        except ScrapeError as exc:
            msg = f"⚠️ *News Agent error* (bahanews)\n{exc}"
            await send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
            print("[scheduler] Telegram message sent.", file=sys.stderr)
            return

        BATCH = 5
        for i in range(0, len(items), BATCH):
            batch = items[i : i + BATCH]

            async def _enrich(item, _drv=driver):
                item.content = await fetch_article_content(_drv, item.url)

            await _asyncio.gather(*[_enrich(item) for item in batch])

        if SCHEDULE_SEARCH:
            kw = SCHEDULE_SEARCH.lower()
            items = [
                it for it in items
                if it.content
                and not it.content.startswith("[ERROR]")
                and not it.content.startswith("[LOGIN")
                and kw in it.content.lower()
            ]

        lines = [f"📰 *baha.com News*" + (f" – {label}" if label else "")]
        lines.append(f"_{len(items)} articles_\n")
        for it in items:
            lines.append(f"• [{it.title}]({it.url})")
        msg = "\n".join(lines)

    # ------------------------------------------------------------------
    # Branch: xnews
    # ------------------------------------------------------------------
    elif SCHEDULE_ENDPOINT == "xnews":
        from app.scraper.x_feed import scrape_x_feed

        lines = [f"🐦 *X Feed*" + (f" – {label}" if label else "")]
        total_tweets = 0

        for handle in TWITTER_HANDLES:
            try:
                tweets = await scrape_x_feed(driver, max_hours=SCHEDULE_HOURS, handle=handle)
            except RuntimeError as exc:
                lines.append(f"\n*{handle}* – ⚠️ error: {exc}")
                continue

            if SCHEDULE_SEARCH:
                kw = SCHEDULE_SEARCH.lower()
                tweets = [
                    t for t in tweets
                    if t.content
                    and not t.content.startswith("[ERROR]")
                    and not t.content.startswith("[LOGIN")
                    and kw in t.content.lower()
                ]

            total_tweets += len(tweets)
            lines.append(f"\n*{handle}* – _{len(tweets)} tweets_")
            for t in tweets:
                snippet = (t.content or "")[:200].replace("\n", " ")
                if len(t.content or "") > 200:
                    snippet += "…"
                lines.append(f"• {snippet}")

        lines.insert(1, f"_{total_tweets} tweets across {len(TWITTER_HANDLES)} handle(s)_")
        msg = "\n".join(lines)

    # ------------------------------------------------------------------
    # Unknown endpoint
    # ------------------------------------------------------------------
    else:
        msg = (
            f"⚠️ *News Agent* – unknown SCHEDULE\_ENDPOINT value: `{SCHEDULE_ENDPOINT}`\n"
            "Accepted values: `summary`, `bahanews`, `xnews`."
        )

    await send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
    print("[scheduler] Telegram message sent.", file=sys.stderr)


async def scheduler_loop() -> None:
    """Entry point – runs forever, posting every SCHEDULE_INTERVAL_MINUTES minutes.

    Silently exits (without error) if the required env vars are not set.
    """
    if not TELEGRAM_BOT_TOKEN:
        print("[scheduler] TELEGRAM_BOT_TOKEN not set – scheduler disabled.", file=sys.stderr)
        return
    if not TELEGRAM_CHAT_ID:
        print("[scheduler] TELEGRAM_CHAT_ID not set – scheduler disabled.", file=sys.stderr)
        return
    if not SCHEDULE_INTERVAL_MINUTES or SCHEDULE_INTERVAL_MINUTES <= 0:
        print("[scheduler] SCHEDULE_INTERVAL_MINUTES not set or <= 0 – scheduler disabled.", file=sys.stderr)
        return

    print(
        f"[scheduler] enabled – endpoint={SCHEDULE_ENDPOINT!r}, "
        f"posting to Telegram every {SCHEDULE_INTERVAL_MINUTES} min "
        f"(hours={SCHEDULE_HOURS}, search={SCHEDULE_SEARCH!r})",
        file=sys.stderr,
    )

    # Wait one full interval before the first post so the browser has time to start
    await asyncio.sleep(SCHEDULE_INTERVAL_MINUTES * 60)

    while True:
        try:
            await _run_once()
        except Exception as exc:
            print(f"[scheduler] unhandled error: {exc}", file=sys.stderr)
        await asyncio.sleep(SCHEDULE_INTERVAL_MINUTES * 60)
