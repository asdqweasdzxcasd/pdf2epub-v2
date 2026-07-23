"""레이아웃 블록 분류: heading, paragraph, figure, table, formula"""

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PIL import Image

from app.pipeline.progress import ProgressCallback

logger = logging.getLogger(__name__)


class BlockType(str, Enum):
    """레이아웃 블록 유형"""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    FIGURE = "figure"
    TABLE = "table"
    FORMULA = "formula"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    LIST_ITEM = "list_item"


@dataclass
class Block:
    """레이아웃 블록"""

    block_type: BlockType
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    confidence: float
    text: str = ""
    image_path: str | None = None  # figure/table/formula 블록의 크롭 이미지 파일명


@dataclass
class PageLayout:
    """페이지 레이아웃 결과"""

    page_num: int
    blocks: list[Block] = field(default_factory=list)


# Surya LayoutPredictor label → BlockType 매핑
_SURYA_LABEL_MAP: dict[str, BlockType] = {
    "Text": BlockType.PARAGRAPH,
    "Section-header": BlockType.HEADING,
    "Title": BlockType.HEADING,
    "Figure": BlockType.FIGURE,
    "Picture": BlockType.FIGURE,
    "Table": BlockType.TABLE,
    "Formula": BlockType.FORMULA,
    "Caption": BlockType.CAPTION,
    "Footnote": BlockType.FOOTNOTE,
    "Page-header": BlockType.PAGE_HEADER,
    "Page-footer": BlockType.PAGE_FOOTER,
    "List-item": BlockType.LIST_ITEM,
    "Text-inline-math": BlockType.PARAGRAPH,
    "Handwriting": BlockType.PARAGRAPH,
    "Form": BlockType.PARAGRAPH,
    "Table-of-contents": BlockType.PARAGRAPH,
}

# 이미지로 크롭해서 저장할 블록 유형 (MVP: 수식은 이미지 크롭 임베드)
_CROP_BLOCK_TYPES = frozenset({BlockType.FIGURE, BlockType.TABLE, BlockType.FORMULA})

# OCR 텍스트를 매칭할 블록 유형
_TEXT_BLOCK_TYPES = frozenset({
    BlockType.HEADING,
    BlockType.PARAGRAPH,
    BlockType.CAPTION,
    BlockType.FOOTNOTE,
    BlockType.LIST_ITEM,
})


class SuryaLayoutEngine:
    """Surya Layout 엔진. 모델을 한 번 로드하고 재사용한다."""

    def __init__(self, device: str = "cpu"):
        os.environ.setdefault("TORCH_DEVICE", device)

        from surya.foundation import FoundationPredictor
        from surya.layout import LayoutPredictor

        logger.info("Surya Layout 모델 로딩 중...")
        foundation = FoundationPredictor()
        self._predictor = LayoutPredictor(foundation)
        logger.info("Surya Layout 모델 로딩 완료")

    def detect_layout(self, images: list[Image.Image]) -> list[list[Block]]:
        """이미지 리스트의 레이아웃을 분석한다.

        Args:
            images: PIL Image 리스트

        Returns:
            페이지별 Block 리스트 (y좌표 기준 정렬)
        """
        results = self._predictor(images)

        all_blocks: list[list[Block]] = []
        for page_result in results:
            blocks: list[Block] = []
            for item in page_result.bboxes:
                label = getattr(item, "label", "Text")
                block_type = _SURYA_LABEL_MAP.get(label, BlockType.PARAGRAPH)
                blocks.append(
                    Block(
                        block_type=block_type,
                        bbox=tuple(item.bbox),
                        confidence=getattr(item, "confidence", 0.0),
                    )
                )
            # y좌표 → x좌표 순으로 정렬 (위에서 아래, 왼쪽에서 오른쪽)
            blocks.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
            all_blocks.append(blocks)

        return all_blocks


def _block_area(block: Block) -> float:
    """블록 bbox의 면적. 작은 블록 우선 매칭에 사용."""
    x1, y1, x2, y2 = block.bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _crop_and_save_block(
    img: Image.Image,
    block: Block,
    figures_dir: Path,
    page_num: int,
    block_idx: int,
) -> str:
    """figure/table/formula 블록을 크롭해서 PNG로 저장한다.

    Args:
        img: 원본 페이지 이미지
        block: 크롭할 블록
        figures_dir: 크롭 이미지 저장 디렉토리
        page_num: 페이지 번호
        block_idx: 블록 인덱스

    Returns:
        저장된 파일명. 크롭 영역이 유효하지 않으면 빈 문자열.
    """
    x1, y1, x2, y2 = [int(v) for v in block.bbox]
    # 이미지 범위 클리핑
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(img.width, x2), min(img.height, y2)

    if x2 <= x1 or y2 <= y1:
        return ""

    cropped = img.crop((x1, y1, x2, y2))
    filename = f"{page_num:04d}_{block_idx:02d}.png"
    save_path = figures_dir / filename
    cropped.save(str(save_path))
    return filename


def _match_ocr_text(block: Block, ocr_lines: list) -> str:
    """블록 bbox 안에 속하는 OCR 라인들의 텍스트를 결합한다.

    OCR 라인의 중심점이 블록 bbox 안에 있으면 매칭된 것으로 판정한다.
    duck typing: ocr_lines 각 원소는 .bbox, .text 속성을 가진다.

    Args:
        block: 레이아웃 블록
        ocr_lines: OCR 라인 리스트 (OcrLine 프로토콜)

    Returns:
        매칭된 텍스트를 줄바꿈으로 결합한 문자열
    """
    bx1, by1, bx2, by2 = block.bbox
    matched: list[str] = []

    for line in ocr_lines:
        lx1, ly1, lx2, ly2 = line.bbox
        # 라인 중심점이 블록 영역 내에 있는지 확인
        cx = (lx1 + lx2) / 2
        cy = (ly1 + ly2) / 2
        if bx1 <= cx <= bx2 and by1 <= cy <= by2:
            matched.append(line.text)

    return "\n".join(matched)


def analyze_layout(
    clean_dir: Path,
    ocr_results: list,  # list[OcrPageResult] (순환 의존 방지를 위해 duck typing)
    temp_dir: Path,
    device: str = "cpu",
    progress_cb: ProgressCallback | None = None,
    page_count: int = 0,
) -> list[PageLayout]:
    """페이지 이미지와 OCR 결과를 결합하여 레이아웃을 분석한다.

    Surya LayoutPredictor로 블록을 분류한 뒤:
    - figure/table/formula 블록은 이미지를 크롭해서 figures/ 에 저장
    - text/heading 등 블록에는 OCR 텍스트를 좌표 매칭으로 할당

    Args:
        clean_dir: 전처리된 이미지 디렉토리 (temp/{job_id}/clean/)
        ocr_results: 페이지별 OCR 결과 (OcrPageResult.lines 에 접근)
        temp_dir: 임시 디렉토리 (temp/{job_id}/)
        device: "cpu" 또는 "cuda"
        progress_cb: 진행률 콜백 (ProgressCallback 프로토콜)
        page_count: 총 페이지 수 (0이면 파일 개수로 자동 결정)

    Returns:
        페이지별 PageLayout 리스트
    """
    figures_dir = temp_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    page_files = sorted(clean_dir.glob("*.png"))
    if not page_files:
        logger.warning("레이아웃 분석할 페이지 이미지가 없음: %s", clean_dir)
        return []

    if not page_count:
        page_count = len(page_files)

    if progress_cb:
        progress_cb.update(75, "layout", "Surya Layout 모델 로딩 중")

    engine = SuryaLayoutEngine(device=device)

    # 배치 처리 (메모리 절약을 위해 4페이지씩)
    batch_size = 4
    all_layouts: list[PageLayout] = []

    for batch_start in range(0, len(page_files), batch_size):
        batch_files = page_files[batch_start : batch_start + batch_size]
        batch_images: list[Image.Image] = []

        # 배치 시작 시점에 진행 메시지 갱신 (UI 멈춤 방지)
        if progress_cb:
            end = min(batch_start + batch_size, page_count)
            pct = 75 + int(batch_start / page_count * 10)
            progress_cb.update(
                pct, "layout", f"레이아웃 처리 중 ({batch_start + 1}~{end}/{page_count})"
            )

        for f in batch_files:
            try:
                img = Image.open(f).convert("RGB")
                batch_images.append(img)
            except Exception as e:
                logger.warning("이미지 로드 실패: %s - %s", f.name, e)
                batch_images.append(Image.new("RGB", (100, 100), "white"))

        try:
            batch_blocks = engine.detect_layout(batch_images)
        except Exception as e:
            logger.error(
                "레이아웃 분석 배치 실패 (page %d~%d): %s",
                batch_start,
                batch_start + len(batch_files) - 1,
                e,
            )
            batch_blocks = [[] for _ in batch_images]

        for j, (page_file, blocks, img) in enumerate(
            zip(batch_files, batch_blocks, batch_images)
        ):
            page_num = batch_start + j

            # 해당 페이지의 OCR 결과 (인덱스 범위 체크)
            page_ocr = (
                ocr_results[page_num] if page_num < len(ocr_results) else None
            )

            # 1) figure/table/formula 블록은 이미지 크롭 저장 (텍스트 매칭과 무관)
            for k, block in enumerate(blocks):
                if block.block_type in _CROP_BLOCK_TYPES:
                    filename = _crop_and_save_block(
                        img, block, figures_dir, page_num, k
                    )
                    if filename:
                        block.image_path = filename

            # 2) 텍스트 블록에 OCR 라인을 매칭한다.
            #
            # 중요: 한 OCR 라인은 한 블록에만 매칭되어야 한다. layout 모델이
            # 한 페이지에 작은 블록 + 그 영역을 덮는 큰 블록을 동시에 detect할 수
            # 있어서, 매칭을 단순히 "bbox 안 들어가면 매칭"으로 하면 같은 라인이
            # 여러 블록에 다 들어가 텍스트가 N배로 중복된다 (스캔 PDF 변환 시
            # 결과가 같은 단락 수십 번 반복되는 버그의 원인).
            #
            # 해결: 면적이 작은 블록부터 매칭하면서 사용된 라인 인덱스를 추적.
            # 큰 블록은 작은 블록이 가져가지 않은 라인만 받는다.
            if page_ocr:
                text_block_indices = [
                    k for k, b in enumerate(blocks)
                    if b.block_type in _TEXT_BLOCK_TYPES
                ]
                text_block_indices.sort(key=lambda k: _block_area(blocks[k]))

                used_line_indices: set[int] = set()
                for k in text_block_indices:
                    block = blocks[k]
                    bx1, by1, bx2, by2 = block.bbox
                    matched: list[tuple[int, str]] = []
                    for i, line in enumerate(page_ocr.lines):
                        if i in used_line_indices:
                            continue
                        lx1, ly1, lx2, ly2 = line.bbox
                        cx = (lx1 + lx2) / 2
                        cy = (ly1 + ly2) / 2
                        if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                            matched.append((i, line.text))

                    if matched:
                        # 원본 라인 순서 유지 (위→아래 읽기 순서)
                        matched.sort(key=lambda m: m[0])
                        block.text = "\n".join(t for _, t in matched)
                        used_line_indices.update(i for i, _ in matched)

            all_layouts.append(PageLayout(page_num=page_num, blocks=blocks))

            # 진행률 보고 (layout 단계: 75~85%)
            if progress_cb:
                pct = 75 + int((page_num + 1) / page_count * 10)
                progress_cb.update(
                    pct, "layout", f"레이아웃 {page_num + 1}/{page_count}"
                )

        # 배치 이미지 메모리 해제
        for img in batch_images:
            img.close()

    return all_layouts
