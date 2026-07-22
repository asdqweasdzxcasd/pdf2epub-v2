"""HTTP 엔드포인트: upload, jobs, download"""

import logging
import re
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Job, get_db

logger = logging.getLogger(__name__)
router = APIRouter()

_redis = Redis.from_url(settings.REDIS_URL)
_queue = Queue(
    settings.RQ_QUEUE,
    connection=_redis,
    default_timeout=settings.JOB_TIMEOUT_SECONDS,
)

# 파일명 안전 문자: 영숫자, 한글, 공백, ._-
_SAFE_TITLE_RE = re.compile(r"[^\w가-힣 ._-]", flags=re.UNICODE)

# 동시 1건 제한 — IP + 세션 쿠키 조합으로 식별
SESSION_COOKIE_NAME = "ebook_session"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1년


def _safe_filename(title: str | None, fallback: str) -> str:
    base = (title or fallback).strip()
    base = _SAFE_TITLE_RE.sub("", base)[:100].strip()
    return base or fallback


def _get_client_ip(request: Request) -> str:
    """nginx forwarded headers를 거친 진짜 클라이언트 IP."""
    if request.client and request.client.host:
        return request.client.host[:64]
    return "unknown"


def _redirect_with_error(message: str, session_cookie: str | None = None) -> RedirectResponse:
    """업로드 에러를 메인 페이지로 redirect + ?error= query로 전달.

    form submit 후 JSON 응답이 그대로 노출되는 문제 회피를 위해, HTTPException
    대신 이 함수로 메인 페이지에 다시 보내고 빨간 박스로 메시지 표시한다.
    """
    encoded = quote(message)
    response = RedirectResponse(
        url=f"{settings.ROOT_PATH}/?error={encoded}",
        status_code=303,
    )
    if session_cookie:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_cookie,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=True,
        )
    return response


@router.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드 가능합니다.")

    # 클라이언트 식별 (IP + 세션 쿠키)
    client_ip = _get_client_ip(request)
    client_session = request.cookies.get(SESSION_COOKIE_NAME)
    if not client_session:
        client_session = uuid4().hex

    # 동시 1건 제한 체크 — 같은 (ip, session)에 진행 중인 잡 있으면 거부
    existing = db.execute(
        select(Job).where(
            Job.client_ip == client_ip,
            Job.client_session == client_session,
            Job.status.in_(("pending", "processing")),
        ).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="이미 진행 중인 변환이 있습니다. 완료 후 다시 시도해주세요.",
        )

    job_id = uuid4().hex
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / f"{job_id}.pdf"

    total = 0
    try:
        with upload_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"파일 크기 초과 (최대 {settings.MAX_UPLOAD_BYTES // (1024 * 1024)}MB)",
                    )
                f.write(chunk)
    except HTTPException:
        upload_path.unlink(missing_ok=True)
        raise
    except Exception:
        upload_path.unlink(missing_ok=True)
        logger.exception("업로드 처리 실패")
        raise HTTPException(
            status_code=500,
            detail="업로드 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        )

    title = Path(file.filename).stem[:256]

    job = Job(
        id=job_id,
        status="pending",
        progress=0,
        upload_path=str(upload_path),
        title=title,
        client_ip=client_ip,
        client_session=client_session,
    )
    db.add(job)
    db.commit()

    _queue.enqueue(
        "app.tasks.convert_job",
        job_id,
        job_id=job_id,
        job_timeout=settings.JOB_TIMEOUT_SECONDS,
    )

    # JS fetch면 JSON 받아서 status_url로 이동, NoScript fallback이면 직접 redirect
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        from fastapi.responses import JSONResponse
        response: RedirectResponse | JSONResponse = JSONResponse(
            content={
                "job_id": job_id,
                "status_url": f"{settings.ROOT_PATH}/status/{job_id}",
            }
        )
    else:
        response = RedirectResponse(
            url=f"{settings.ROOT_PATH}/status/{job_id}", status_code=303
        )
    # 세션 쿠키 발행 (없었던 경우 새 값)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=client_session,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return response


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다")
    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "stage": job.progress_stage,
        "message": job.progress_message,
        "title": job.title,
        "page_count": job.page_count,
        "error": job.error,
    }


@router.get("/download/{job_id}")
async def download(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다")
    if job.status != "done":
        raise HTTPException(status_code=409, detail="변환이 완료되지 않았습니다")
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=410, detail="결과 파일이 만료되었습니다")

    filename = f"{_safe_filename(job.title, job_id)}.epub"
    return FileResponse(
        job.output_path,
        media_type="application/epub+zip",
        filename=filename,
    )
