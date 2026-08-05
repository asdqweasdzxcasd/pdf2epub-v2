"""PDF -> EPUB 변환 파이프라인 실행기.

CLI(scripts/convert.py)와 RQ 워커(app/tasks.py) 양쪽에서 공유.
호출자가 progress 객체와 임시 디렉토리 라이프사이클을 관리한다.
"""

import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from app.config import settings
from app.pipeline.epub_build import build_epub
from app.pipeline.imgproc import (
    chroma_coverage,
    ink_coverage,
    is_blank_page,
    trim_uniform_margins,
)
from app.pipeline.layout import PageLayout
from app.pipeline.ocr_api import MistralOcrClient, OcrApiError
from app.pipeline.ocr_layout import build_layouts_from_ocr
from app.pipeline.pdf_render import RENDER_DPI, render
from app.pipeline.progress import ProgressCallback
from app.pipeline.refine import refine_small_text
from app.pipeline.text_extract import build_layouts_from_text
from app.pipeline.toc import extract_toc, find_first_chapter_divider_page

logger = logging.getLogger(__name__)

_UPLOAD_JPEG_QUALITY = 88  # 업로드 PDF 내부 이미지 인코딩 품질 (크기 절감용)


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
    trim: bool = True,
    refine: bool = True,
) -> PipelineResult:
    """PDF를 EPUB으로 변환한다.

    ocr_mode:
        auto — 이미지 PDF이고 MISTRAL_API_KEY가 있으면 OCR API 사용
        api  — 강제로 OCR API 사용 (키 없으면 에러)
        off  — V1 동작 (이미지 페이지는 PNG 임베드)

    trim:
        OCR API 경로(use_api)에서만 의미가 있다. True(기본)면 페이지 이미지의
        균일 여백을 먼저 잘라내 OCR에 보낸다 — Mistral 내부 정규화(~1020px)
        전에 여백을 제거하면 콘텐츠 글자가 상대적으로 커져 인식률이 좋아진다.
        좌표계 일치를 위해 build_layouts_from_ocr에도 같은 트림된 이미지
        경로를 넘긴다(Mistral이 본 이미지 = bbox 기준 = 크롭 소스).

    refine:
        OCR API 경로에서만 의미가 있다. True(기본)면 캡션/각주 블록을
        개별 크롭해 재-OCR하는 2-pass 보정을 수행한다(작은 글씨가
        Mistral의 내부 정규화로 뭉개지는 문제 대응). 조각들은 PDF 1개로
        묶어 1회 호출로 처리한다(무료 티어 rate limit 대응).
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
        logger.info("OCR API 경로 사용 (mode=%s, trim=%s)", ocr_mode, trim)
        client = MistralOcrClient(api_key=api_key, model=settings.OCR_MODEL)
        if trim:
            ocr_pdf_path, ocr_page_images = _prepare_trimmed_input(
                render_result.page_images, temp_dir
            )
        else:
            ocr_pdf_path, ocr_page_images = pdf_path, render_result.page_images
        pages = client.process_pdf(ocr_pdf_path, progress=progress)
        page_layouts = build_layouts_from_ocr(
            pages,
            page_images=ocr_page_images,
            figures_dir=figures_dir,
            progress=progress,
        )
        # 표지 판정은 refine보다 먼저 — 페이지 0이 표지 이미지로 통째 대체되면
        # 그 안에 있던 CAPTION 등 refine 대상 블록도 함께 사라지는 게 맞다
        # (원본 render 페이지 이미지 기준으로 판정 — 트림 여부와 무관).
        page_layouts = _replace_cover_page(page_layouts, render_result, figures_dir)
        # 앞부분(front matter) 디자인 페이지(차례 등) 판정도 refine보다 먼저
        # — 페이지가 이미지로 통째 대체되면 그 안의 CAPTION 등 refine 대상
        # 블록도 함께 사라지는 게 맞다 (표지 판정과 같은 이유).
        page_layouts = _replace_front_matter_design_pages(
            page_layouts, render_result, figures_dir
        )
        # refine은 _fill_missing_pages 전에 실행 — fallback은 CAPTION이
        # 없어 refine 대상이 아님 (순서 바꾸면 좌표계 가정이 깨짐)
        if refine:
            try:
                n = refine_small_text(
                    page_layouts, ocr_page_images, client, temp_dir, pages=pages
                )
                progress.update(82, "refine", f"작은 글씨 보정 {n}건")
            except OcrApiError:
                # 보정은 부가 기능 — 실패해도 1차 결과로 변환을 계속한다
                logger.warning("작은 글씨 보정 실패 — 1차 OCR 결과 유지", exc_info=True)
        # fallback은 항상 render_result(원본)를 쓴다 — 전체 페이지 임베드는
        # 좌표 정합이 필요 없어 트림 여부와 무관하게 허용된다
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

    if use_api:
        # 목차 확보 이후에만 실행 -- 장 구분 페이지의 heading 텍스트("3장" 등)가
        # 목차 생성에 필요한데, 여기서 이미지로 대체하면 텍스트가 사라진다.
        page_layouts = _replace_design_pages(page_layouts, render_result, figures_dir)

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


def _prepare_trimmed_input(page_images: list[Path], temp_dir: Path) -> tuple[Path, list[Path]]:
    """페이지 이미지들의 여백을 트림하고, 그 이미지들로 이미지-only PDF를 조립한다.

    반환된 PDF 경로는 client.process_pdf에, 이미지 경로 리스트는
    build_layouts_from_ocr(page_images=...)에 그대로 전달해야 한다 —
    Mistral이 실제로 본 이미지와 블록 bbox 기준, 크롭 소스가 모두
    같은 좌표계를 공유해야 하기 때문이다.

    크롭 소스(trimmed_paths)는 항상 무손실 PNG로 저장한다 — 나중에 블록
    bbox로 그림을 잘라낼 때 화질 손실이 누적되면 안 되기 때문이다. 반면
    업로드용 PDF(trimmed_pdf_path)에는 같은 이미지를 JPEG(quality=88)로
    인코딩해 넣는다 — Mistral API에 보내는 업로드 크기만 줄이는 용도이며,
    픽셀 치수는 PNG와 동일하게 유지해 좌표 정합을 깨지 않는다.
    """
    trimmed_dir = temp_dir / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)

    trimmed_paths: list[Path] = []
    trimmed_sizes: list[tuple[int, int]] = []
    trimmed_jpeg_bytes: list[bytes] = []
    for i, src in enumerate(page_images):
        with Image.open(src) as img:
            trimmed = trim_uniform_margins(img)

            # 크롭 소스 — 무손실 PNG (좌표 정합의 기준 픽셀 치수도 여기서 온다)
            dst = trimmed_dir / f"page_{i:04d}.png"
            trimmed.save(dst)
            trimmed_paths.append(dst)
            trimmed_sizes.append(trimmed.size)

            # 업로드 PDF 내부 인코딩 — JPEG는 알파를 지원하지 않으므로 RGB 변환
            rgb_for_upload = (
                trimmed.convert("RGB") if trimmed.mode != "RGB" else trimmed
            )
            buf = io.BytesIO()
            rgb_for_upload.save(buf, format="JPEG", quality=_UPLOAD_JPEG_QUALITY)
            trimmed_jpeg_bytes.append(buf.getvalue())

    trimmed_pdf_path = temp_dir / "trimmed.pdf"
    doc = fitz.open()
    try:
        for jpeg_bytes, (w, h) in zip(trimmed_jpeg_bytes, trimmed_sizes):
            # Convert pixel dimensions (from 300 DPI render) to PDF points
            pt_w = w * 72 / RENDER_DPI
            pt_h = h * 72 / RENDER_DPI

            # Guard against page size limits (PyMuPDF spec: 14400pt)
            if pt_h > 14000 or pt_w > 14000:
                logger.warning(
                    "Page size exceeds 14000pt after conversion: %.1fpt x %.1fpt",
                    pt_w, pt_h
                )

            page = doc.new_page(width=pt_w, height=pt_h)
            page.insert_image(fitz.Rect(0, 0, pt_w, pt_h), stream=jpeg_bytes)
        doc.save(str(trimmed_pdf_path))
    finally:
        doc.close()

    return trimmed_pdf_path, trimmed_paths


def _fill_missing_pages(page_layouts, render_result, figures_dir):
    """OCR 응답에서 누락됐거나 블록이 빈 페이지를 페이지 PNG 임베드로 보정한다.

    API가 일부 페이지를 못 읽어도 책 내용이 조용히 유실되면 안 된다 —
    V1과 동일하게 페이지 이미지라도 보존한다.

    단, 챕터 구분용 백지(단색) 페이지는 예외다 — OCR 블록 0개로 오는 흔한
    경우인데, 그 페이지 이미지를 그대로 임베드하면 무의미한 빈 이미지가
    남는다. is_blank_page로 거의 단색인지 확인해서 단색이면 그 페이지는
    (빈 PageLayout도 만들지 않고) 결과에서 통째로 제외한다. 그림 한 장만
    있는 페이지(사진 전면 페이지)는 단색이 아니므로 영향받지 않는다.
    """
    from app.pipeline.text_extract import embed_page_as_figure

    by_num = {pl.page_num: pl for pl in page_layouts}
    filled = []
    for i in range(render_result.page_count):
        layout = by_num.get(i)
        if layout is not None and layout.blocks:
            filled.append(layout)
            continue

        if _is_blank_page_image(render_result, i):
            logger.info("페이지 %d: 단색 백지 페이지 — 임베드 제외", i + 1)
            continue

        block = embed_page_as_figure(render_result, i, figures_dir)
        if block is not None:
            logger.warning("페이지 %d: OCR 결과 없음 — 페이지 이미지로 대체", i + 1)
            filled.append(PageLayout(page_num=i, blocks=[block]))
        elif layout is not None:
            filled.append(layout)  # PNG도 없으면 빈 레이아웃이라도 유지
    return filled


_COVER_INK_THRESHOLD = 0.5  # 이 이상 비백색이면 "디자인된 표지"로 판정


def _replace_cover_page(page_layouts, render_result, figures_dir):
    """첫 페이지(index 0)가 디자인된 표지로 판정되면 그 페이지의 텍스트
    블록들을 페이지 이미지 전체를 담은 FIGURE 블록 하나로 대체한다.

    판정: 페이지 0 이미지의 ink_coverage(비백색 비율)가 0.5 이상이면 표지.
    단, is_blank_page(단색 페이지)이면 챕터 구분용 백지 등을 표지로 오인하지
    않도록 판정에서 제외한다.

    표지가 아니거나 페이지 0 이미지를 확인/임베드할 수 없으면 원래
    page_layouts를 그대로 반환한다(한 페이지 판정 실패가 변환 전체를
    죽이면 안 됨).
    """
    page_images = getattr(render_result, "page_images", []) or []
    if not page_images:
        return page_layouts
    src = page_images[0]
    if src is None or not Path(src).exists():
        return page_layouts

    try:
        with Image.open(src) as img:
            if is_blank_page(img):
                return page_layouts
            coverage = ink_coverage(img)
    except Exception:
        return page_layouts

    if coverage < _COVER_INK_THRESHOLD:
        return page_layouts

    # 지연 import — text_extract가 run 모듈을 다시 참조하지 않아 순환 의존은
    # 없지만, 이 표지 경로에서만 필요한 헬퍼라 다른 모듈들과 같은 관례를 따른다.
    from app.pipeline.text_extract import embed_page_as_figure

    cover_block = embed_page_as_figure(render_result, 0, figures_dir)
    if cover_block is None:
        return page_layouts

    replaced: list[PageLayout] = []
    found = False
    for layout in page_layouts:
        if layout.page_num == 0:
            replaced.append(PageLayout(page_num=0, blocks=[cover_block]))
            found = True
        else:
            replaced.append(layout)
    if not found:
        replaced.append(PageLayout(page_num=0, blocks=[cover_block]))
        replaced.sort(key=lambda pl: pl.page_num)
    return replaced


_FRONT_MATTER_CHROMA_MIN = 0.04  # 이 이상 유채색 비율이면 컬러 디자인 페이지로 판정


def _replace_front_matter_design_pages(page_layouts, render_result, figures_dir):
    """장 구분 페이지보다 앞(front matter)에 있고 유채색 비율이 높은 디자인
    페이지(차례, 책소개 등)를, 페이지 이미지 전체를 담은 FIGURE 블록 하나로
    대체한다 — 컬러 챕터 밴드로 조판된 디자인은 텍스트로 재조판하면
    사라지므로 표지와 같은 방식(페이지 이미지 임베드)으로 보존한다.

    판정 조건 둘 다 필요:
    1. find_first_chapter_divider_page로 찾은 첫 "N장" 구분 페이지보다
       앞이어야 한다 — 장 구분 페이지를 못 찾으면(다른 책 형식) 이 함수는
       아무것도 바꾸지 않고 page_layouts를 그대로 반환한다.
    2. chroma_coverage(_FRONT_MATTER_CHROMA_MIN 이상)여야 한다 — 실측:
       디자인 페이지 0.048~0.098, 본문 텍스트 페이지 0.0006~0.0035. 그림이
       있는 본문 페이지는 0.049까지 올라갈 수 있어 유채색 비율만으로는
       부족하므로 반드시 조건 1(장 구분 페이지 이전)과 함께 쓴다 — 장 구분
       "이후"의 그림 페이지는 이 함수가 건드리지 않는다.

    이미 FIGURE 블록 하나로만 이뤄진 페이지(표지 대체 등으로 이미 이미지가
    된 경우)는 건드리지 않는다(중복 임베드 방지).
    """
    first_divider_page = find_first_chapter_divider_page(page_layouts)
    if first_divider_page is None:
        return page_layouts

    page_images = getattr(render_result, "page_images", []) or []

    # 지연 import — 표지 판정 경로와 같은 관례.
    from app.pipeline.text_extract import embed_page_as_figure

    replaced: list[PageLayout] = []
    for layout in page_layouts:
        if layout.page_num >= first_divider_page or _is_single_figure_layout(layout):
            replaced.append(layout)
            continue
        if layout.page_num >= len(page_images):
            replaced.append(layout)
            continue
        src = page_images[layout.page_num]
        if src is None or not Path(src).exists():
            replaced.append(layout)
            continue

        try:
            with Image.open(src) as img:
                coverage = chroma_coverage(img)
        except Exception:
            replaced.append(layout)
            continue

        if coverage < _FRONT_MATTER_CHROMA_MIN:
            replaced.append(layout)
            continue

        block = embed_page_as_figure(render_result, layout.page_num, figures_dir)
        if block is None:
            replaced.append(layout)
            continue
        replaced.append(PageLayout(page_num=layout.page_num, blocks=[block]))

    return replaced


_DESIGN_PAGE_INK_THRESHOLD = 0.5  # 이 이상 비백색이면 전면 컬러 "디자인 페이지"로 판정


def _replace_design_pages(page_layouts, render_result, figures_dir):
    """잉크 비율(ink_coverage)이 높은 모든 페이지를 페이지 이미지 전체를 담은
    FIGURE 블록 하나로 대체한다 -- 표지뿐 아니라 장 구분 페이지("1장" 등
    전면 컬러 표지 격 페이지)도 텍스트만 뽑으면 밋밋해지므로 원본 디자인을
    이미지로 보존한다.

    반드시 extract_toc 호출 뒤에만 실행해야 한다 -- 장 구분 페이지의 heading
    텍스트("N장", 장 이름)가 목차 생성(_extract_chapter_dividers)에 쓰이는데,
    여기서 이미지로 먼저 대체하면 그 텍스트가 사라져 목차가 비어버린다.

    판정 조건 둘 다 필요:
    1. ink_coverage >= _DESIGN_PAGE_INK_THRESHOLD (실측: 표지 0.947, 장
       구분 페이지 0.985~0.993, 본문 텍스트 페이지 0.016~0.15).
    2. is_blank_page(단색)가 아니어야 한다 -- 챕터 구분용 단색 백지는 이
       규칙이 아니라 _fill_missing_pages가 별도로 제외한다(이미지로
       대체하면 무의미한 빈 이미지가 남으므로).

    대체 시 PageLayout.page_num은 반드시 보존한다 --
    _split_into_chapters(epub_build.py)가 page_num 기준으로 챕터를 나누므로,
    이를 지키지 않으면 챕터 분할이 깨진다.

    이미 FIGURE 블록 하나로만 이뤄진 페이지(표지/앞부분 디자인 페이지 대체
    등으로 이미 이미지가 된 경우)는 건드리지 않는다(중복 임베드 방지).
    """
    page_images = getattr(render_result, "page_images", []) or []

    # 지연 import -- 표지/앞부분 디자인 판정 경로와 같은 관례.
    from app.pipeline.text_extract import embed_page_as_figure

    replaced: list[PageLayout] = []
    for layout in page_layouts:
        if _is_single_figure_layout(layout):
            replaced.append(layout)
            continue
        if layout.page_num >= len(page_images):
            replaced.append(layout)
            continue
        src = page_images[layout.page_num]
        if src is None or not Path(src).exists():
            replaced.append(layout)
            continue

        try:
            with Image.open(src) as img:
                if is_blank_page(img):
                    replaced.append(layout)
                    continue
                coverage = ink_coverage(img)
        except Exception:
            replaced.append(layout)
            continue

        if coverage < _DESIGN_PAGE_INK_THRESHOLD:
            replaced.append(layout)
            continue

        block = embed_page_as_figure(render_result, layout.page_num, figures_dir)
        if block is None:
            replaced.append(layout)
            continue
        replaced.append(PageLayout(page_num=layout.page_num, blocks=[block]))

    return replaced


def _is_single_figure_layout(layout) -> bool:
    """레이아웃이 이미 FIGURE 블록 하나로만 이뤄져 있는지 확인한다(표지
    대체 등으로 이미 이미지 페이지가 된 경우 중복 처리를 피하기 위한 가드)."""
    if len(layout.blocks) != 1:
        return False
    block_type = layout.blocks[0].block_type
    value = block_type.value if hasattr(block_type, "value") else str(block_type)
    return value == "figure"


def _is_blank_page_image(render_result, page_num: int) -> bool:
    """render_result.page_images[page_num]가 거의 단색 페이지인지 확인한다.

    페이지 PNG가 없거나 열 수 없으면 False (판정 불가 -> 기존 동작대로
    임베드 시도하게 둔다).
    """
    page_images = getattr(render_result, "page_images", []) or []
    if page_num >= len(page_images):
        return False
    src_path = page_images[page_num]
    if src_path is None or not Path(src_path).exists():
        return False
    try:
        with Image.open(src_path) as img:
            return is_blank_page(img)
    except Exception:
        return False
