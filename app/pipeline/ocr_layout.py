"""Mistral OCR 응답을 V1 레이아웃 자료구조(PageLayout)로 변환하는 어댑터.

Mistral 블록 타입 문서:
text, title, list, table, image, equation, caption, code,
references, aside_text, header, footer, signature
"""

import logging
from pathlib import Path

from PIL import Image

from app.pipeline.imgproc import sample_block_background, strip_chromatic_frame
from app.pipeline.layout import Block, BlockType, PageLayout
from app.pipeline.markdown_inline import parse_heading

logger = logging.getLogger(__name__)

_TYPE_MAP: dict[str, BlockType] = {
    "text": BlockType.PARAGRAPH,
    "title": BlockType.HEADING,
    "list": BlockType.LIST_ITEM,
    "table": BlockType.TABLE,
    "image": BlockType.FIGURE,
    "equation": BlockType.FORMULA,
    "caption": BlockType.CAPTION,
    "code": BlockType.CODE,
    "references": BlockType.FOOTNOTE,
    "aside_text": BlockType.ASIDE,
    "header": BlockType.PAGE_HEADER,
    "footer": BlockType.PAGE_FOOTER,
    "signature": BlockType.FIGURE,
}

# 페이지 PNG에서 크롭해 이미지로 임베드하는 타입 (Task 2에서 구현)
_CROP_TYPES = frozenset({BlockType.FIGURE, BlockType.FORMULA})

# 배경색(Block.bg)을 추출할 텍스트 계열 타입 -- 그림/표/수식은 대상이 아니다
# (그 자체가 이미지로 크롭되므로 배경 개념이 없음).
_BG_TYPES = frozenset({
    BlockType.HEADING,
    BlockType.PARAGRAPH,
    BlockType.LIST_ITEM,
    BlockType.CODE,
    BlockType.CAPTION,
})

# bbox 바깥으로 확장할 여유(px) — Mistral bbox가 다이어그램 경계선보다
# 타이트해 콘텐츠가 잘리는 문제 보정. 여분 배경은 strip_chromatic_frame이
# 다시 트림해 제거한다 (Task 4)
_PAD_OUT = 6

# 문장이 이미 끝났다고 볼 수 있는 종결 문자(공백 제외 후 마지막 글자 기준)
_SENTENCE_END_CHARS = frozenset('.!?…"\')]》」')

# 한국어 조사/어미 후보 — 뒤 블록이 이 글자로 시작하고 바로 다음 글자가
# 공백이면 "앞 블록에 붙는 조사"로 보고 병합 대상으로 삼는다.
# (예: "는 소켓" -> "는"은 앞 단어 HttpClient에 붙는 조사)
# 주의: 문자 단위 판정이라 "이 책은..."처럼 실제로는 새 문장이 시작하는
# 경우도 오탐으로 병합될 수 있다 — 보수적 규칙이라도 완벽하지는 않다.
_JOSA_CHARS = frozenset("는은이가를을에의도로와과만라며고서나든")


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
        page_img = _open_page_image(page_num, page_images)
        try:
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
                level = 0
                if btype is BlockType.HEADING:
                    level, content = parse_heading(content)
                bg = None
                if btype in _BG_TYPES and page_img is not None:
                    bg = _sample_block_bg(raw, page, page_img)
                blocks.append(Block(
                    block_type=btype, bbox=bbox, confidence=1.0, text=content,
                    level=level, bg=bg,
                ))
        finally:
            if page_img is not None:
                page_img.close()
        blocks = _merge_paragraph_blocks(blocks)
        layouts.append(PageLayout(page_num=page_num, blocks=blocks))

    return layouts


def _starts_with_josa_candidate(text: str) -> bool:
    """text가 조사 후보 글자로 시작하고, 그 다음 글자가 공백인지(또는 글자가
    하나뿐인지) 본다. 이 경우 "앞 단어에 붙는 조사"로 보고 병합 신호로 삼는다."""
    if not text or text[0] not in _JOSA_CHARS:
        return False
    return len(text) == 1 or text[1] == " "


def _starts_with_lowercase_latin(text: str) -> bool:
    """영문 소문자로 시작하면 영문 문장이 중간에 끊긴 것으로 본다."""
    return bool(text) and text[0].isascii() and text[0].isalpha() and text[0].islower()


def _should_merge(prev: Block, nxt: Block) -> bool:
    """연속된 두 블록이 OCR이 잘못 쪼갠 같은 문단인지 보수적으로 판정한다.

    셋 다 만족해야 병합 대상:
    1) 둘 다 PARAGRAPH이고 bg가 같음 (다른 배경 박스의 문단이면 병합 안 함)
    2) 앞 블록이 문장 종결 부호로 끝나지 않음
    3) 뒤 블록이 이어짐 신호(조사 후보+공백, 또는 소문자 라틴)로 시작함
    """
    if prev.block_type is not BlockType.PARAGRAPH or nxt.block_type is not BlockType.PARAGRAPH:
        return False
    if prev.bg != nxt.bg:
        return False
    prev_text = prev.text.rstrip()
    if not prev_text or prev_text[-1] in _SENTENCE_END_CHARS:
        return False
    nxt_text = nxt.text.lstrip()
    if not nxt_text:
        return False
    return _starts_with_josa_candidate(nxt_text) or _starts_with_lowercase_latin(nxt_text)


def _merge_text(prev_text: str, next_text: str) -> str:
    """두 블록의 텍스트를 이어붙인다.

    뒤 텍스트가 한글 조사로 시작하면 공백 없이 붙이고(앞 단어에 붙는 조사이므로),
    소문자 라틴으로 시작하면 공백 하나를 넣어 붙인다(끊긴 영문 단어 사이 구분).
    """
    prev = prev_text.rstrip()
    nxt = next_text.lstrip()
    if nxt and nxt[0] in _JOSA_CHARS:
        return prev + nxt
    return prev + " " + nxt


def _merge_paragraph_blocks(blocks: list[Block]) -> list[Block]:
    """OCR이 줄 단위로 쪼갠 문단(연속 PARAGRAPH 블록)을 이어붙인다.

    Mistral OCR은 가끔 한 문단을 여러 text 블록으로 나눠 반환한다(줄바꿈마다
    새 블록). 렌더링 시 블록 1개 = 문단 1개이므로 그대로 두면 문장이 조사로
    시작하는 등 부자연스럽게 끊긴 문단이 여러 개 생긴다. 3개 이상 연속도
    순차 병합으로 하나로 합쳐진다.
    """
    merged: list[Block] = []
    for blk in blocks:
        if merged and _should_merge(merged[-1], blk):
            prev = merged[-1]
            merged[-1] = Block(
                block_type=prev.block_type,
                bbox=prev.bbox,
                confidence=prev.confidence,
                text=_merge_text(prev.text, blk.text),
                level=prev.level,
                bg=prev.bg,
            )
            continue
        merged.append(blk)
    return merged


def _open_page_image(page_num: int, page_images: list[Path]) -> Image.Image | None:
    """배경색 추출용 페이지 이미지를 연다. 없거나 못 열면 None(호출자는 bg를
    포기하고 계속 진행 -- 한 페이지 실패가 변환 전체를 죽이면 안 됨)."""
    if page_num >= len(page_images):
        return None
    src = page_images[page_num]
    if src is None or not Path(src).exists():
        return None
    try:
        return Image.open(src).convert("RGB")
    except Exception:
        return None


def _sample_block_bg(raw: dict, page: dict, page_img: Image.Image) -> tuple[int, int, int] | None:
    """raw 블록의 bbox를 페이지 이미지 픽셀 좌표로 변환해 배경색을 추출한다.

    scale_bbox가 요구하는 좌표 키가 없거나 타입이 잘못됐으면 None(bbox 파싱과
    동일한 관용 -- 이 블록의 배경 추출만 포기하고 텍스트는 그대로 유지).
    """
    try:
        box = scale_bbox(raw, page_img.width, page_img.height, page.get("dimensions") or {})
    except (KeyError, TypeError, ValueError):
        return None
    return sample_block_background(page_img, box)


_EXTEND_CAP = 40  # 변당 콘텐츠 경계 확장 상한(px)
_EXTEND_MIN_CONTENT = 3  # 경계 행/열을 "콘텐츠 걸림"으로 볼 최소 어두운/유채색 픽셀 수


def _extend_to_content_boundary(img: Image.Image, x0: int, y0: int, x1: int, y1: int):
    """bbox 경계에 콘텐츠 픽셀이 걸려 있으면 배경 행/열이 나올 때까지 확장한다.

    Mistral bbox는 다이어그램 자체의 테두리선을 몇 px 못 담는 undershoot이
    있다 (실측: 하단 가로 테두리가 잘려 세로선 stub만 남음). 고정 패딩만으로는
    undershoot 크기가 가변이라 부족하고, 과하게 키우면 이웃 캡션을 물어온다.
    경계 행/열에 비배경 픽셀이 남아있는 동안만 1px씩, 변당 최대 _EXTEND_CAP까지
    확장해 "콘텐츠가 배경으로 닫히는 지점"에서 정확히 멈춘다.
    """
    import numpy as np

    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    h, w = arr.shape[:2]
    dark_or_colored = (arr.min(axis=2) < 200) | ((arr.max(axis=2) - arr.min(axis=2)) >= 15)

    def edge_has_content(rows, cols):
        return int(dark_or_colored[rows, cols].sum()) >= _EXTEND_MIN_CONTENT

    for _ in range(_EXTEND_CAP):
        if y1 < h and edge_has_content(y1 - 1, slice(x0, x1)):
            y1 += 1
        else:
            break
    for _ in range(_EXTEND_CAP):
        if x1 < w and edge_has_content(slice(y0, y1), x1 - 1):
            x1 += 1
        else:
            break
    for _ in range(_EXTEND_CAP):
        if y0 > 0 and edge_has_content(y0, slice(x0, x1)):
            y0 -= 1
        else:
            break
    for _ in range(_EXTEND_CAP):
        if x0 > 0 and edge_has_content(slice(y0, y1), x0):
            x0 -= 1
        else:
            break
    return x0, y0, x1, y1


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
            x0, y0, x1, y1 = _extend_to_content_boundary(img, x0, y0, x1, y1)
            x0, y0 = max(0, x0 - _PAD_OUT), max(0, y0 - _PAD_OUT)
            x1, y1 = min(w, x1 + _PAD_OUT), min(h, y1 + _PAD_OUT)
            crop = img.crop((x0, y0, x1, y1))
            crop = strip_chromatic_frame(crop)  # 채도 프레임 제거 + 여백 트림
            filename = f"page_{page_num:04d}_blk_{blk_idx:03d}.png"
            crop.save(figures_dir / filename)
            return filename
    except Exception:
        logger.warning("블록 크롭 실패 p%d blk%d", page_num, blk_idx, exc_info=True)
        return None
