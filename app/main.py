"""
app/main.py
~~~~~~~~~~~
FastAPI application factory.

This is the only place where:
  • the FastAPI instance is created,
  • the browser lifespan is wired in,
  • all routers are registered.

Keep this file thin – business logic belongs in the sub-packages.
"""
from contextlib import asynccontextmanager
import asyncio

from dotenv import load_dotenv
load_dotenv()  # load .env before any config is imported

from fastapi import FastAPI

from app.api.routes import router
from app.browser.session import browser_lifespan
from app.scheduler import scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with browser_lifespan():
        task = asyncio.create_task(scheduler_loop())
        try:
            yield
        finally:
            task.cancel()


_DESCRIPTION = """
## News Agent API

Scrapes live news from **baha.com** and the **X (Twitter)** profile feed using a
headless Chrome browser that inherits cookies from your local Chrome profile.

### Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/bahanews` | Latest baha.com articles with full body text |
| `GET /api/xnews` | Latest tweets from @MarioNawfal |
| `GET /api/status` | Browser connection & authentication diagnostics |
| `GET /api/debug-page` | Inspect CSS selectors on any baha.com page |

### Common query parameters

- **`hours`** – return only items from the last N hours  
- **`search`** – keyword filter applied to article / tweet content (case-insensitive)

### Interactive docs

- Swagger UI → [`/docs`](/docs)  
- ReDoc → [`/redoc`](/redoc)
"""

app = FastAPI(
    title="News Agent API",
    description=_DESCRIPTION,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.include_router(router)
