"""
app/telegram.py
~~~~~~~~~~~~~~~
Send messages to a Telegram chat via the Bot API.
Handles Telegram's 4096-char message limit by chunking automatically.
"""
import sys

import httpx

_API = "https://api.telegram.org"
_CHUNK = 4000  # leave 96 chars of headroom below the 4096 hard limit


async def send_message(bot_token: str, chat_id: str, text: str) -> None:
    """Send *text* to *chat_id* using *bot_token*.

    If the text exceeds 4000 characters it is split into multiple messages.
    Uses ``parse_mode=Markdown`` so **bold** and _italic_ render correctly.
    """
    if not bot_token or not chat_id:
        print("[telegram] bot_token or chat_id missing – message not sent.", file=sys.stderr)
        return

    url = f"{_API}/bot{bot_token}/sendMessage"
    chunks = [text[i : i + _CHUNK] for i in range(0, len(text), _CHUNK)]

    async with httpx.AsyncClient(timeout=30) as client:
        for chunk in chunks:
            try:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "Markdown",
                    },
                )
                if not resp.is_success:
                    print(
                        f"[telegram] sendMessage failed {resp.status_code}: {resp.text}",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[telegram] sendMessage error: {exc}", file=sys.stderr)
