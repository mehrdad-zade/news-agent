"""
app/models/tweet.py
~~~~~~~~~~~~~~~~~~~
Pydantic data models for the X (Twitter) feed API.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class TweetItem(BaseModel):
    url: str
    #: ISO 8601 UTC timestamp from X's <time datetime="..."> attribute.
    posted_at: str
    #: Human-readable relative label e.g. "2h ago", "34m ago".
    time_posted: str
    content: str = ""
    #: Internal field used for chronological filtering/sorting; excluded from output.
    hours_ago: float = 999.0


class TweetResponse(BaseModel):
    status: str
    time_frame_hours: Optional[int]
    search: Optional[str]
    total_found: int
    tweets: List[TweetItem]
