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

from fastapi import FastAPI

from app.api.routes import router
from app.browser.session import browser_lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with browser_lifespan():
        yield


app = FastAPI(title="Baha News Agent", lifespan=lifespan)
app.include_router(router)
