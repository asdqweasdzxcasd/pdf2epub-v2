"""Mistral OCR 응답을 V1 레이아웃 자료구조(PageLayout)로 변환하는 어댑터.

Mistral 블록 타입 문서:
text, title, list, table, image, equation, caption, code,
references, aside_text, header, footer, signature
"""

import logging
from pathlib import Path

from PIL import Image

from app.pipeline.imgproc import trim_uniform_margins
from app.pipeline.layout import Block, BlockType, PageLayout

logger = logging.getLogger(__name__)

_TYPE_MAP: dict[str, BlockType] = {
    "text": BlockType.PARAGRAPH,
    "title": BlockType.HEADING,
    "list": BlockType.LIST_ITEM,
    "table": BlockType.TABLE,
    "image": BlockType.FIGURE,
    "equation": BlockType.FORMULA,
    "caption": BlockType.CAPTION,
    "code": BlockType.PARAGRAPH,
    "references": BlockType.FOOTNOTE,
    "aside_text": BlockType.PARAGRAPH,
    "header": BlockType.PAGE_HEADER,
    "footer": BlockType.PAGE_FOOTER,
    "signature": BlockType.FIGURE,
}

# 페이지 PNG에서 크롭해 이미지로 임베드하는 타입 (Task 2에서 구현)
_CROP_TYPES = frozenset({BlockType.FIGURE, BlockType.FORMULA})


def map_block_type(mistral_type: str) -> BlockType:
    """미지 타입은 PARAGRAPH 폴백 — API 스펙 확장에 대비."""
    return _TYPE_MAP.get(mistral_type, BlockType.PARAGRAPH)


def scale_bbox(block: dict, img_w: int, img_h: int, page_dim: dict) -> tuple[int, int, int, int]:
    """블록 좌표를 렌더 이미지 픽셀 좌표로 변환.

    좌표가 0~1 정규화인지 페이지 픽셀 기준인지 응답에 따라 다를 수 있어
    최대값 1.5 이하이면 정규화로 간주한다 (M1 벤치에서 검증된 로직).

    좌표 키가 없는 블록에는 KeyError를 던진다 — 호출자가 try/except로
    가드하는 것이 계약이다 (build_layouts_from_ocr의 bbox 파싱 참조).
    """
    x0, y0 = block["top_left_x"], block["top_left_y"]
    x1, y1 = block["bottom_right_x"], block["bottom_right_y"]
    if max(x1, y1) <= 1.5:
        return int(x0 * img_w), int(y0 * img_h), int(x1 * img_w), int(y1 * img_h)
    ref_w = page_dim.get("width") or img_w
    ref_h = page_dim.get("height") or img_h
    sx, sy = img_w / ref_w, img_h / ref_h
    return int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)


def build_layouts_from_ocr(
    pages: list[dict],
    page_images: list[Path],
    figures_dir: Path,
    progress=None,
) -> list[PageLayout]:
    """Mistral 응답 pages 배열을 PageLayout 리스트로 변환한다.

    블록은 응답의 reading order를 그대로 따른다 (재정렬 안 함).
    텍스트 블록의 bbox는 Mistral 응답 좌표계 원본 그대로 보존한다 —
    현재 하류 소비자(epub_build, toc)는 bbox를 쓰지 않으며, 픽셀 좌표가
    필요한 소비자는 scale_bbox()로 변환해야 한다 (크롭 경로가 그렇게 함).
    image/equation 블록 크롭은 Task 2에서 구현되었다.
    """
    figures_dir.mkdir(parents=True, exist_ok=True)
    layouts: list[PageLayout] = []

    for page in pages:
        page_num = page.get("index", 0)
        blocks: list[Block] = []
        for raw in page.get("blocks") or []:
            btype = map_block_type(raw.get("type", ""))
            if btype in _CROP_TYPES:
                img_path = _crop_block(
                    raw, page, page_num, len(blocks), page_images, figures_dir
                )
                if img_path is None:
                    continue
                blocks.append(Block(
                    block_type=btype,
                    bbox=(0.0, 0.0, 0.0, 0.0),
                    confidence=1.0,
                    image_path=img_path,
                ))
                continue
            content = (raw.get("content") or "").strip()
            if not content:
                continue
            try:
                bbox = (
                    float(raw["top_left_x"]), float(raw["top_left_y"]),
                    float(raw["bottom_right_x"]), float(raw["bottom_right_y"]),
                )
            except (KeyError, TypeError, ValueError):
                bbox = (0.0, 0.0, 0.0, 0.0)
            blocks.append(Block(
                block_type=btype, bbox=bbox, confidence=1.0, text=content,
            ))
        layouts.append(PageLayout(page_num=page_num, blocks=blocks))

    return layouts


def _crop_block(
    raw: dict,
    page: dict,
    page_num: int,
    blk_idx: int,
    page_images: list[Path],
    figures_dir: Path,
) -> str | None:
    """블록 bbox 영역을 페이지 PNG에서 크롭해 저장하고 파일명을 반환.

    페이지 PNG가 없거나 bbox가 비정상이면 None (해당 블록은 조용히 스킵 —
    한 블록 실패가 변환 전체를 죽이면 안 됨).
    """
    # 호출자는 page_images를 원본 PDF 페이지 번호로 인덱싱 가능한 전체 리스트로 전달한다
    if page_num >= len(page_images):
        return None
    src = page_images[page_num]
    if src is None or not Path(src).exists():
        return None
    try:
        with Image.open(src) as img:
            w, h = img.size
            x0, y0, x1, y1 = scale_bbox(raw, w, h, page.get("dimensions") or {})
            if x1 <= x0 or y1 <= y0:
                return None
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            crop = img.crop((x0, y0, x1, y1))
            crop = trim_uniform_margins(crop, pad=2)  # 페이지 장식 테두리 제거
            filename = f"page_{page_num:04d}_blk_{blk_idx:03d}.png"
            crop.save(figures_dir / filename)
            return filename
    except Exception:
        logger.warning("블록 크롭 실패 p%d blk%d", page_num, blk_idx, exc_info=True)
        return None
