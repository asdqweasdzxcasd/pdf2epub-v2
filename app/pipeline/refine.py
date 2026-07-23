"""2-pass 작은 글씨(캡션·각주) 재-OCR 보정.

Mistral OCR은 페이지 이미지를 내부적으로 ~1020px로 정규화해서 처리하기 때문에
캡션·각주처럼 작은 글씨는 뭉개져서 읽힌다. 검증된 해법: 해당 블록 영역만
크롭해 단독으로 다시 보내면 그 조각이 자체 정규화를 받아 완벽하게 읽힌다.

무료 티어 rate limit(분당 2요청) 때문에 크롭 조각들을 한데 모아
MistralOcrClient.process_images로 1회 호출한다.
"""

import logging
from pathlib import Path

from PIL import Image

from app.pipeline.layout import Block, BlockType, PageLayout
from app.pipeline.ocr_api import MistralOcrClient
from app.pipeline.ocr_layout import scale_bbox

logger = logging.getLogger(__name__)

# 재-OCR 대상 블록 유형. FIGURE/FORMULA는 크롭 시 bbox를 (0,0,0,0)으로
# 비우는 기존 ocr_layout 동작 때문에 자연스럽게 대상에서 제외된다.
_REFINE_TYPES = frozenset({BlockType.CAPTION, BlockType.FOOTNOTE})
_CROP_PAD = 6
_EMPTY_BBOX = (0.0, 0.0, 0.0, 0.0)


def refine_small_text(
    page_layouts: list[PageLayout],
    page_images: list[Path],
    client: MistralOcrClient,
    temp_dir: Path,
    pages: list[dict] | None = None,
) -> int:
    """CAPTION/FOOTNOTE 블록을 개별 크롭해 재-OCR하고 텍스트를 교체한다.

    블록의 bbox는 Mistral 원좌표계이고 page_images는 트림된 페이지
    이미지들이다 — Mistral이 실제로 본 이미지가 곧 트림된 이미지이므로
    두 좌표계는 일치한다(run.py의 trim=True 경로가 이를 보장).

    Args:
        page_layouts: build_layouts_from_ocr의 결과. 교체된 블록은
            in-place로 block.text가 갱신된다.
        page_images: page_num으로 인덱싱 가능한 트림된 페이지 이미지 경로.
        client: 크롭 조각들을 재-OCR할 MistralOcrClient.
        temp_dir: 임시 디렉토리 — 크롭은 temp_dir/refine/ 에 저장한다.
        pages: Mistral 원본 응답 pages 배열(선택). 블록 bbox가 정규화되지
            않은 절대 좌표일 때 스케일 기준(dimensions)을 제공하기 위함 —
            _crop_block과 동일한 규칙(scale_bbox)을 쓰기 위해 필요하다.

    Returns:
        텍스트가 교체된 블록 수. bbox가 없거나(0,0,0,0) 크롭에 실패하거나
        재-OCR 응답이 빈 문자열이면 해당 블록은 스킵되고(원본 유지) 카운트에
        포함되지 않는다.
    """
    dims_by_page: dict[int, dict] = {
        page.get("index", i): (page.get("dimensions") or {})
        for i, page in enumerate(pages or [])
    }

    refine_dir = temp_dir / "refine"
    refine_dir.mkdir(parents=True, exist_ok=True)

    targets: list[Block] = []
    crop_paths: list[Path] = []

    for layout in page_layouts:
        if layout.page_num >= len(page_images):
            continue
        src = page_images[layout.page_num]
        if src is None or not Path(src).exists():
            continue
        page_dim = dims_by_page.get(layout.page_num, {})
        for idx, block in enumerate(layout.blocks):
            if block.block_type not in _REFINE_TYPES:
                continue
            if block.bbox == _EMPTY_BBOX:
                continue
            crop_path = _crop_for_refine(
                block, src, page_dim, refine_dir, layout.page_num, idx
            )
            if crop_path is None:
                continue
            targets.append(block)
            crop_paths.append(crop_path)

    if not targets:
        return 0

    texts = client.process_images(crop_paths)

    replaced = 0
    for block, text in zip(targets, texts):
        stripped = (text or "").strip()
        if stripped:
            block.text = stripped
            replaced += 1

    return replaced


def _crop_for_refine(
    block: Block,
    src: Path,
    page_dim: dict,
    refine_dir: Path,
    page_num: int,
    blk_idx: int,
) -> Path | None:
    """블록 bbox를 pad px 여유를 두고 크롭해 저장, 경로를 반환한다.

    scale_bbox는 ocr_layout._crop_block과 동일한 규칙으로 재사용한다 —
    좌표 환산 로직이 갈라지면 크롭 좌표가 어긋난다.
    """
    try:
        with Image.open(src) as img:
            w, h = img.size
            raw = {
                "top_left_x": block.bbox[0], "top_left_y": block.bbox[1],
                "bottom_right_x": block.bbox[2], "bottom_right_y": block.bbox[3],
            }
            x0, y0, x1, y1 = scale_bbox(raw, w, h, page_dim)
            if x1 <= x0 or y1 <= y0:
                return None
            x0, y0 = max(0, x0 - _CROP_PAD), max(0, y0 - _CROP_PAD)
            x1, y1 = min(w, x1 + _CROP_PAD), min(h, y1 + _CROP_PAD)
            crop = img.crop((x0, y0, x1, y1))
            filename = f"page_{page_num:04d}_blk_{blk_idx:03d}.png"
            dst = refine_dir / filename
            crop.save(dst)
            return dst
    except Exception:
        logger.warning("보정 크롭 실패 p%d blk%d", page_num, blk_idx, exc_info=True)
        return None
