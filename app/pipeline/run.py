"""PDF -> EPUB 변환 파이프라인 실행기.

CLI(scripts/convert.py)와 RQ 워커(app/tasks.py) 양쪽에서 공유.
호출자가 progress 객체와 임시 디렉토리 라이프사이클을 관리한다.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.pipeline.epub_build import build_epub
from app.pipeline.layout import PageLayout
from app.pipeline.ocr_api import MistralOcrClient
from app.pipeline.ocr_layout import build_layouts_from_ocr
from app.pipeline.pdf_render import render
from app.pipeline.progress import ProgressCallback
from app.pipeline.text_extract import build_layouts_from_text
from app.pipeline.toc import extract_toc

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    page_count: int
    ocr_needed: bool
    toc_count: int


def run_pipeline(
    pdf_path: Path,
    output_path: Path,
    temp_dir: Path,
    device: str,
    title: str,
    progress: ProgressCallback,
    ocr_mode: str = "auto",
) -> PipelineResult:
    """PDF를 EPUB으로 변환한다.

    ocr_mode:
        auto — 이미지 PDF이고 MISTRAL_API_KEY가 있으면 OCR API 사용
        api  — 강제로 OCR API 사용 (키 없으면 에러)
        off  — V1 동작 (이미지 페이지는 PNG 임베드)
    """

    logger.info("PDF 렌더링 시작: %s", pdf_path)
    render_result = render(pdf_path, temp_dir, progress_cb=progress)
    logger.info(
        "렌더링 완료: %d페이지, OCR 필요: %s",
        render_result.page_count,
        render_result.ocr_needed,
    )

    figures_dir = temp_dir / "figures"

    # 환경변수 우선, 없으면 .env(settings) — 테스트의 monkeypatch.setenv와
    # 운영의 .env 패턴을 모두 지원한다
    api_key = os.environ.get("MISTRAL_API_KEY") or settings.MISTRAL_API_KEY
    use_api = ocr_mode == "api" or (
        ocr_mode == "auto" and render_result.ocr_needed and bool(api_key)
    )

    if use_api:
        logger.info("OCR API 경로 사용 (mode=%s)", ocr_mode)
        client = MistralOcrClient(api_key=api_key, model=settings.OCR_MODEL)
        pages = client.process_pdf(pdf_path, progress=progress)
        page_layouts = build_layouts_from_ocr(
            pages,
            page_images=render_result.page_images,
            figures_dir=figures_dir,
            progress=progress,
        )
        page_layouts = _fill_missing_pages(page_layouts, render_result, figures_dir)
    else:
        if render_result.ocr_needed:
            logger.info("이미지 PDF — V1 경로(페이지 PNG 임베드)로 처리")
        progress.update(20, "extract", "텍스트/이미지 추출")
        page_layouts = build_layouts_from_text(
            render_result, figures_dir, progress
        )

    logger.info("목차 추출 시작")
    toc_entries = extract_toc(
        pdf_path,
        page_layouts=page_layouts,
        progress_cb=progress,
    )
    logger.info("목차 항목: %d개", len(toc_entries))

    logger.info("EPUB 빌드 시작")
    build_epub(
        page_layouts=page_layouts,
        toc_entries=toc_entries,
        figures_dir=figures_dir,
        output_path=output_path,
        title=title,
        progress_cb=progress,
    )

    return PipelineResult(
        page_count=render_result.page_count,
        ocr_needed=render_result.ocr_needed,
        toc_count=len(toc_entries),
    )


def _fill_missing_pages(page_layouts, render_result, figures_dir):
    """OCR 응답에서 누락됐거나 블록이 빈 페이지를 페이지 PNG 임베드로 보정한다.

    API가 일부 페이지를 못 읽어도 책 내용이 조용히 유실되면 안 된다 —
    V1과 동일하게 페이지 이미지라도 보존한다.
    """
    from app.pipeline.text_extract import embed_page_as_figure

    by_num = {pl.page_num: pl for pl in page_layouts}
    filled = []
    for i in range(render_result.page_count):
        layout = by_num.get(i)
        if layout is not None and layout.blocks:
            filled.append(layout)
            continue
        block = embed_page_as_figure(render_result, i, figures_dir)
        if block is not None:
            logger.warning("페이지 %d: OCR 결과 없음 — 페이지 이미지로 대체", i + 1)
            filled.append(PageLayout(page_num=i, blocks=[block]))
        elif layout is not None:
            filled.append(layout)  # PNG도 없으면 빈 레이아웃이라도 유지
    return filled
