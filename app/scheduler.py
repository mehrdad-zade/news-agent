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
    SCHEDULE_HOURS,
    SCHEDULE_INTERVAL_MINUTES,
    SCHEDULE_SEARCH,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from app.telegram import send_message


async def _run_once() -> None:
    """Scrape, summarise, and post one update to Telegram."""
    # Import here to avoid circular imports at module load time
    from app.browser.session import get_cdp_context
    from app.core.summarise import build_summary

    driver = get_cdp_context()
    if driver is None:
        print("[scheduler] browser driver not ready – skipping run.", file=sys.stderr)
        return

    print(
        f"[scheduler] running summary "
        f"(hours={SCHEDULE_HOURS}, search={SCHEDULE_SEARCH!r})",
        file=sys.stderr,
    )

    result = await build_summary(driver, hours=SCHEDULE_HOURS, search=SCHEDULE_SEARCH)

    if result.get("status") == "error":
        msg = f"⚠️ *News Agent error*\n{result.get('error', 'unknown error')}"
    else:
        label_parts = []
        if SCHEDULE_HOURS:
            label_parts.append(f"last {SCHEDULE_HOURS}h")
        if SCHEDULE_SEARCH:
            label_parts.append(f"`{SCHEDULE_SEARCH}`")
        label = " · ".join(label_parts)

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
        f"[scheduler] enabled – posting to Telegram every {SCHEDULE_INTERVAL_MINUTES} min "
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
