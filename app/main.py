"""FastAPI 앱 엔트리포인트"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api import router as api_router
from app.config import settings
from app.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.TEMP_DIR).mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="ebook-converter",
    root_path=settings.ROOT_PATH,
    lifespan=lifespan,
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "max_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024),
            "root_path": settings.ROOT_PATH,
        },
    )


@app.get("/status/{job_id}", response_class=HTMLResponse)
async def status_page(request: Request, job_id: str):
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "job_id": job_id,
            "root_path": settings.ROOT_PATH,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
