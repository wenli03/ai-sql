from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from .api import router

app = FastAPI(title=config.APP_TITLE, version="0.1.0")
app.include_router(router)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html")


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)
