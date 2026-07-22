"""Mistral OCR 응답 → PageLayout 어댑터 테스트"""
import json
from pathlib import Path

from app.pipeline.layout import BlockType
from app.pipeline.ocr_layout import build_layouts_from_ocr, map_block_type, scale_bbox

FIXTURE = Path(__file__).parent / "fixtures" / "mistral_response_sample.json"


def test_map_block_type_전체_매핑():
    assert map_block_type("text") is BlockType.PARAGRAPH
    assert map_block_type("title") is BlockType.HEADING
    assert map_block_type("list") is BlockType.LIST_ITEM
    assert map_block_type("table") is BlockType.TABLE
    assert map_block_type("image") is BlockType.FIGURE
    assert map_block_type("equation") is BlockType.FORMULA
    assert map_block_type("caption") is BlockType.CAPTION
    assert map_block_type("references") is BlockType.FOOTNOTE
    assert map_block_type("header") is BlockType.PAGE_HEADER
    assert map_block_type("footer") is BlockType.PAGE_FOOTER
    assert map_block_type("code") is BlockType.PARAGRAPH
    assert map_block_type("aside_text") is BlockType.PARAGRAPH
    assert map_block_type("signature") is BlockType.FIGURE
    # 미지의 타입은 PARAGRAPH로 폴백 (API가 타입을 추가해도 죽지 않게)
    assert map_block_type("something_new") is BlockType.PARAGRAPH


def test_scale_bbox_정규화_좌표():
    blk = {"top_left_x": 0.1, "top_left_y": 0.2,
           "bottom_right_x": 0.5, "bottom_right_y": 0.9}
    assert scale_bbox(blk, 1000, 2000, {}) == (100, 400, 500, 1800)


def test_scale_bbox_픽셀_좌표():
    blk = {"top_left_x": 100, "top_left_y": 200,
           "bottom_right_x": 500, "bottom_right_y": 900}
    dim = {"width": 1000, "height": 2000}
    assert scale_bbox(blk, 1000, 2000, dim) == (100, 200, 500, 900)


def test_build_layouts_실측_픽스처(tmp_path):
    pages = json.loads(FIXTURE.read_text())["pages"]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)

    assert len(layouts) == 2
    assert layouts[0].page_num == pages[0]["index"]
    # 텍스트 블록에 content가 옮겨졌는지
    all_blocks = [b for l in layouts for b in l.blocks]
    text_blocks = [b for b in all_blocks
                   if b.block_type in (BlockType.PARAGRAPH, BlockType.HEADING)]
    assert text_blocks, "텍스트 블록이 하나도 없음"
    assert all(b.text.strip() for b in text_blocks)


def test_build_layouts_블록_없는_페이지는_비어있다(tmp_path):
    pages = [{"index": 0, "blocks": [], "dimensions": {}, "markdown": ""}]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)
    assert len(layouts) == 1
    assert layouts[0].blocks == []


def _make_page_png(tmp_path, w=200, h=400, color=(255, 0, 0)):
    from PIL import Image
    p = tmp_path / "page_000.png"
    img = Image.new("RGB", (w, h), (255, 255, 255))
    # 좌상단 1/4 영역을 칠해서 크롭 검증에 사용
    for x in range(w // 2):
        for y in range(h // 2):
            img.putpixel((x, y), color)
    img.save(p)
    return p


def test_image_블록_크롭(tmp_path):
    from PIL import Image
    page_png = _make_page_png(tmp_path)
    figures = tmp_path / "figures"
    pages = [{
        "index": 0,
        "dimensions": {},
        "markdown": "",
        "blocks": [{
            "type": "image",
            "top_left_x": 0.0, "top_left_y": 0.0,
            "bottom_right_x": 0.5, "bottom_right_y": 0.5,
            "content": "",
        }],
    }]
    layouts = build_layouts_from_ocr(pages, page_images=[page_png], figures_dir=figures)

    blk = layouts[0].blocks[0]
    assert blk.block_type is BlockType.FIGURE
    assert blk.image_path == "page_0000_blk_000.png"
    crop = Image.open(figures / blk.image_path)
    assert crop.size == (100, 200)  # 원본 200x400의 좌상단 절반
    assert crop.getpixel((10, 10)) == (255, 0, 0)


def test_페이지_PNG_없으면_크롭_블록은_건너뛴다(tmp_path):
    pages = [{
        "index": 0, "dimensions": {}, "markdown": "",
        "blocks": [{"type": "image", "top_left_x": 0.0, "top_left_y": 0.0,
                    "bottom_right_x": 0.5, "bottom_right_y": 0.5, "content": ""}],
    }]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path / "f")
    assert layouts[0].blocks == []  # 죽지 않고 조용히 스킵


def test_잘못된_bbox는_건너뛴다(tmp_path):
    page_png = _make_page_png(tmp_path)
    pages = [{
        "index": 0, "dimensions": {}, "markdown": "",
        "blocks": [{"type": "image", "top_left_x": 0.9, "top_left_y": 0.9,
                    "bottom_right_x": 0.1, "bottom_right_y": 0.1, "content": ""}],
    }]
    layouts = build_layouts_from_ocr(pages, page_images=[page_png], figures_dir=tmp_path / "f")
    assert layouts[0].blocks == []  # 역전된 bbox → 스킵


def test_equation_블록도_크롭된다(tmp_path):
    page_png = _make_page_png(tmp_path)
    figures = tmp_path / "figures"
    pages = [{
        "index": 0, "dimensions": {}, "markdown": "",
        "blocks": [{"type": "equation", "top_left_x": 0.0, "top_left_y": 0.0,
                    "bottom_right_x": 0.5, "bottom_right_y": 0.5, "content": ""}],
    }]
    layouts = build_layouts_from_ocr(pages, page_images=[page_png], figures_dir=figures)
    blk = layouts[0].blocks[0]
    assert blk.block_type is BlockType.FORMULA
    assert blk.image_path and (figures / blk.image_path).exists()
