"""PDF 북마크 / 휴리스틱 기반 목차 추출"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from app.pipeline.markdown_inline import to_plain

logger = logging.getLogger(__name__)

# 장 구분 페이지의 title: "3장" 처럼 숫자+장 만 단독으로 있는 heading.
# "1장 시작"처럼 장 이름이 같은 블록에 붙어 있는 경우는 매칭하지 않는다
# (그런 경우는 챕터 이름이 별도 heading 블록으로 오지 않으므로 폴백 경로가 처리한다).
_CHAPTER_DIVIDER_RE = re.compile(r"^\s*\d+\s*장\s*$")

_MAX_TITLE_LEN = 100


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

    실제 전공책은 heading(title) 블록이 수백 개 나오지만 진짜 장 경계는
    "3장" 처럼 숫자+장 단독 heading이 있는 구분 페이지뿐이다. 그런 장 구분
    heading이 문서에 하나라도 있으면 그것만 챕터 경계로 삼는다
    (`_extract_chapter_dividers`). 하나도 없으면 다른 책 형식이 깨지지
    않도록 기존 폴백(페이지당 첫 heading, level ≤ 1)을 사용한다
    (`_extract_fallback_headings`).

    주의: "장 구분 후보가 존재했는가"(has_candidates)와 "유효 항목이
    남았는가"(entries)는 서로 다른 질문이다. 장 구분 heading이 문서에
    있었지만 전부 목차 페이지(한 페이지에 2개 이상 몰림)라서 걸러졌다면
    entries는 비어있어도 has_candidates는 True다. 이 경우 폴백으로
    넘어가면 방금 무시하려던 그 목차 페이지들이 폴백(페이지당 첫 heading)
    에 의해 다시 챕터로 들어오므로, 폴백을 쓰지 말고 빈 목차를 반환한다.

    duck typing 사용:
    - page_layouts: list[PageLayout]
      - PageLayout.page_num: int
      - PageLayout.blocks: list[Block]
    - Block.block_type.value: str ("heading" 등)
    - Block.text: str
    - Block.level: int
    """
    has_candidates, entries = _extract_chapter_dividers(page_layouts)
    if has_candidates:
        return entries
    return _extract_fallback_headings(page_layouts)


def _extract_chapter_dividers(page_layouts: list) -> tuple[bool, list[TocEntry]]:
    """"N장" 단독 heading을 챕터 경계로 삼아 목차를 만든다.

    장 구분 페이지는 보통 장 번호("3장")와 장 이름("성능에 핵심인 DB")이
    별개 heading 블록으로 온다. 장 번호 heading 다음에 오는 heading 블록의
    텍스트를 이어붙여 "3장 성능에 핵심인 DB" 를 목차 제목으로 만든다
    (다음 heading이 없으면 장 번호만 사용).

    한 페이지에 장 구분 heading이 2개 이상이면(예: 앞부분 목차 페이지에
    "1장","2장","3장"이 몰려 나오는 경우) 그 페이지는 목차 페이지로 보고
    전부 무시한다. 다만 그런 페이지도 "장 구분 후보가 있었다"는 사실
    자체는 has_candidates로 별도 반환한다 -- 호출부가 폴백 오발동을
    막는 데 쓴다.

    Returns:
        (has_candidates, entries): has_candidates는 문서 어딘가에 "N장"
        형태 heading이 하나라도 있었는지 (그 페이지가 목차 페이지로
        걸러졌더라도 True). entries는 실제로 채택된 목차 항목.
    """
    entries: list[TocEntry] = []
    has_candidates = False
    for layout in page_layouts:
        headings = [
            (idx, block)
            for idx, block in enumerate(layout.blocks)
            if block.block_type.value == "heading" and block.text.strip()
        ]
        divider_positions = [
            idx
            for idx, block in headings
            if _CHAPTER_DIVIDER_RE.match(block.text.strip())
        ]
        if not divider_positions:
            # 이 페이지엔 장 구분 후보 없음
            continue
        if len(divider_positions) != 1:
            # 2개 이상이면 목차 페이지 -> 항목은 무시하지만 후보는 있었다고 기록
            has_candidates = True
            continue

        has_candidates = True
        divider_idx = divider_positions[0]
        divider_text = layout.blocks[divider_idx].text.strip()

        next_heading_text = None
        for idx, block in headings:
            if idx > divider_idx:
                next_heading_text = block.text.strip()
                break

        title = f"{divider_text} {next_heading_text}" if next_heading_text else divider_text
        entries.append(
            TocEntry(
                title=_clip_title(to_plain(title)),
                page_num=layout.page_num,
                level=1,
            )
        )

    return has_candidates, entries


def _extract_fallback_headings(page_layouts: list) -> list[TocEntry]:
    """장 구분 heading이 없는 문서용 폴백: 페이지당 첫 heading을 챕터 경계로 삼는다.

    Mistral OCR은 소제목도 title로 분류하므로 페이지당 첫 heading만
    챕터 경계로 삼는다 — 그렇지 않으면 한 페이지 안의 소제목들까지
    전부 TOC 항목이 되어 목차가 과도하게 파편화된다.

    추가로 level이 0 또는 1인 heading만 목차 항목으로 채택한다 (level 0은
    레벨 정보가 없는 V1 경로 호환, level 1은 최상위 챕터). level 2 이상
    (마크다운 `##` 이하 소제목)은 본문에 <h2> 등으로만 남고 목차엔 들어가지
    않는다. 페이지당 첫 heading이 level 2 이상이면 그 페이지는 목차 경계가
    되지 못한다(그 페이지에 level 0/1 heading이 없다는 뜻이므로 건너뜀).
    """
    entries = []
    for layout in page_layouts:
        for block in layout.blocks:
            if block.block_type.value == "heading" and block.text.strip():
                if block.level not in (0, 1):
                    break  # 페이지당 첫 heading이 소제목 -> 이 페이지는 목차 경계 아님
                text = _clip_title(to_plain(block.text.strip()))
                entries.append(
                    TocEntry(
                        title=text,
                        page_num=layout.page_num,
                        level=block.level or 1,
                    )
                )
                break  # 페이지당 첫 heading만 채택, 나머지는 건너뜀

    return entries


def _clip_title(text: str) -> str:
    """너무 긴 목차 제목은 잘라낸다."""
    if len(text) > _MAX_TITLE_LEN:
        return text[: _MAX_TITLE_LEN - 3] + "..."
    return text
