"""PDF 북마크 / 휴리스틱 기반 목차 추출"""

import logging
from dataclasses import dataclass
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)


@dataclass
class TocEntry:
    """목차 항목"""

    title: str
    page_num: int  # 0-indexed
    level: int = 1


def extract_toc(
    pdf_path: Path,
    page_layouts: list = None,
    progress_cb=None,
) -> list[TocEntry]:
    """PDF에서 목차를 추출한다.

    우선순위:
    1. PDF 북마크가 있으면 그대로 사용
    2. 레이아웃 결과에서 heading 블록 추출 (폰트 크기 기반 H1)
    3. 둘 다 실패 시 빈 리스트 반환 (목차 없는 단순 본문)

    Args:
        pdf_path: PDF 파일 경로
        page_layouts: layout.py의 PageLayout 리스트 (선택)
        progress_cb: 진행률 콜백 (ProgressCallback 프로토콜)

    Returns:
        TocEntry 리스트 (없으면 빈 리스트)
    """
    if progress_cb:
        progress_cb.update(85, "toc", "목차 추출 중...")

    # 1. PDF 북마크
    toc = _extract_from_bookmarks(pdf_path)
    if toc:
        logger.info("PDF 북마크에서 목차 %d개 추출", len(toc))
        if progress_cb:
            progress_cb.update(90, "toc", f"목차 {len(toc)}개 (북마크)")
        return toc

    # 2. 레이아웃 heading 블록
    if page_layouts:
        toc = _extract_from_headings(page_layouts)
        if toc:
            logger.info("Heading 블록에서 목차 %d개 추출", len(toc))
            if progress_cb:
                progress_cb.update(90, "toc", f"목차 {len(toc)}개 (heading)")
            return toc

    # 3. 목차 없음 - 빈 리스트 반환 (본문 그대로 보여줌)
    logger.info("목차를 찾지 못함 - 단일 본문으로 처리")
    if progress_cb:
        progress_cb.update(90, "toc", "목차 없음 (본문만)")
    return []


def _extract_from_bookmarks(pdf_path: Path) -> list[TocEntry]:
    """PDF 북마크(outline)에서 목차를 추출한다.

    fitz.Document.get_toc()는 [[level, title, page], ...] 형식을 반환한다.
    page는 1-indexed이므로 0-indexed로 변환한다.
    """
    try:
        doc = fitz.open(str(pdf_path))
        bookmarks = doc.get_toc(simple=True)
        doc.close()

        if not bookmarks:
            return []

        entries = []
        for level, title, page in bookmarks:
            title = title.strip()
            if title:
                entries.append(
                    TocEntry(
                        title=title,
                        page_num=max(0, page - 1),  # 1-indexed → 0-indexed
                        level=level,
                    )
                )
        return entries
    except Exception as e:
        logger.warning("북마크 추출 실패: %s", e)
        return []


def _extract_from_headings(page_layouts: list) -> list[TocEntry]:
    """레이아웃 분석 결과에서 heading 블록을 목차로 변환한다.

    Mistral OCR은 소제목도 title로 분류하므로 페이지당 첫 heading만
    챕터 경계로 삼는다 — 그렇지 않으면 한 페이지 안의 소제목들까지
    전부 TOC 항목이 되어 목차가 과도하게 파편화된다.

    duck typing 사용:
    - page_layouts: list[PageLayout]
      - PageLayout.page_num: int
      - PageLayout.blocks: list[Block]
    - Block.block_type.value: str ("heading" 등)
    - Block.text: str
    """
    entries = []
    for layout in page_layouts:
        for block in layout.blocks:
            if block.block_type.value == "heading" and block.text.strip():
                text = block.text.strip()
                # 너무 긴 heading은 잘라낸다
                if len(text) > 100:
                    text = text[:97] + "..."
                entries.append(
                    TocEntry(
                        title=text,
                        page_num=layout.page_num,
                        level=1,
                    )
                )
                break  # 페이지당 첫 heading만 채택, 나머지는 건너뜀

    return entries
