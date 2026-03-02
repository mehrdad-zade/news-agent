"""
app/models/news.py
~~~~~~~~~~~~~~~~~~~
Pydantic data models for the news API.
"""
from typing import List, Optional

from pydantic import BaseModel


class NewsItem(BaseModel):
    title: str
    url: str
    time_posted: str
    content: str = ""
    #: Internal field used for chronological sorting; excluded from API output.
    hours_ago: float = 999.0


class NewsResponse(BaseModel):
    status: str
    time_frame_hours: Optional[int]
    total_found: int
    news: List[NewsItem]
