"""RQ 워커가 실행하는 변환 잡"""

import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.db import Job, SessionLocal
from app.device.runtime import detect
from app.pipeline.run import run_pipeline

logger = logging.getLogger(__name__)


class DbProgress:
    """jobs 테이블에 진행률을 갱신하는 ProgressCallback 구현."""

    __slots__ = ("job_id",)

    def __init__(self, job_id: str):
        self.job_id = job_id

    def update(self, progress: int, stage: str, message: str = "") -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if job is None:
                return
            job.progress = max(0, min(100, progress))
            job.progress_stage = stage[:32]
            job.progress_message = message[:256] if message else None
            db.commit()


def convert_job(job_id: str) -> None:
    """RQ 워커 엔트리포인트. 잡 ID로 DB 조회 → 변환 → 결과 저장."""

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.error("잡을 찾을 수 없음: %s", job_id)
            return
        upload_path = Path(job.upload_path)
        title = job.title or job_id
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        db.commit()

    output_path = Path(settings.OUTPUT_DIR) / f"{job_id}.epub"
    Path(settings.TEMP_DIR).mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f"ebook-{job_id}-", dir=settings.TEMP_DIR))

    progress = DbProgress(job_id)
    runtime = detect()
    device = runtime.torch_device

    try:
        result = run_pipeline(upload_path, output_path, temp_dir, device, title, progress)

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "done"
                job.output_path = str(output_path)
                job.progress = 100
                job.progress_stage = "done"
                job.progress_message = (
                    f"{result.page_count}페이지, 목차 {result.toc_count}개"
                )
                job.page_count = result.page_count
                job.finished_at = datetime.now(timezone.utc)
                db.commit()

        logger.info("변환 완료: %s -> %s", job_id, output_path)

    except Exception as e:
        logger.exception("변환 실패: %s", job_id)
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error = (str(e) or e.__class__.__name__)[:2000]
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        # 업로드 원본 PDF 즉시 삭제 (변환 끝났으니 더 이상 필요 없음)
        try:
            upload_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("업로드 PDF 삭제 실패: %s", upload_path)
