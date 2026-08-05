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


def _is_decorative_noise(text: str) -> bool:
    """한 글자짜리 라틴 문자·기호 블록인지 판정한다 (장식 요소 오인식).

    책의 챕터 마커 배지(초록 상자 안의 'B' 아이콘 등)가 title/text 블록으로
    잡혀 본문에 외톨이 문단으로 반복 등장했다(실측 55회). 한국어 기술서
    본문에서 한 글자짜리 라틴 문자나 기호가 독립 문단·제목일 수는 없으므로
    버린다. 한글 한 글자("그"), 숫자, 두 글자 이상("IP")은 의미를 가질 수
    있으므로 보존한다 — 규칙을 좁게 유지해 정상 텍스트 손실을 막는다.
    """
    t = text.strip()
    if len(t) != 1:
        return False
    return not (t.isdigit() or "\uac00" <= t <= "\ud7a3")


def build_layouts_from_ocr(
    pages: list[dict],
    page_images: list[Path],
    figures_dir: Path,
    progress=None,
) -> list[PageLayout]:
    r"""Mistral 응답 pages 배열을 PageLayout 리스트로 변환한다.

    블록은 응답의 reading order를 그대로 따른다 (재정렬 안 함).
    텍스트 블록의 bbox는 Mistral 응답 좌표계 원본 그대로 보존한다 —
    현재 하류 소비자(epub_build, toc)는 bbox를 쓰지 않으며, 픽셀 좌표가
    필요한 소비자는 scale_bbox()로 변환해야 한다 (크롭 경로가 그렇게 함).
    image/equation 블록 크롭은 Task 2에서 구현되었다.

    연속 PARAGRAPH 블록을 이어붙이는 병합은 하지 않는다 (예전에 있었으나
    제거됨). 실측 캐시(ebook-converter/testdata/ocr-cache)로 확인한 결과
    Mistral OCR은 한 문단 전체를 블록 하나로 주고 그 안의 지면 줄바꿈만
    `\n`으로 보존한다 -- 즉 "문단이 여러 블록으로 쪼개지는" 문제 자체가
    실제로는 드물다. 반면 예전 병합 휴리스틱(연속 블록 + 문장 미종결 +
    조사/소문자 시작)을 실측 데이터에 그대로 적용해 보면 검출된 36건 전부가
    오탐이었다 -- 색인 페이지의 표제어들(예: "Event-Driven Architecture
    310" + "eventual consistency 133"), text로 오분류된 코드 줄들(Java/SQL
    라인이 순서대로 병합됨), TOC/제목처럼 보이는 짧은 줄들. 진짜 원인(지면
    줄바꿈)은 app/pipeline/epub_build.py가 블록 텍스트를 렌더링할 때
    처리한다(app/pipeline/text_merge.split_soft_wrapped_paragraphs).
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
                if _is_decorative_noise(parse_heading(content)[1]):
                    continue  # 장식 배지 등 한 글자 노이즈
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
        layouts.append(PageLayout(page_num=page_num, blocks=blocks))

    return layouts


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
