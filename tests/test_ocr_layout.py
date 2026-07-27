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
    all_blocks = [b for layout in layouts for b in layout.blocks]
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


def _make_page_png_with_box(tmp_path, w=200, h=400, box=(50, 50, 149, 249), color=(128, 128, 128)):
    """흰 배경 페이지에 지정 box(inclusive) 영역만 색을 칠한 PNG를 만든다.

    color는 채도 0(회색조)으로 둬야 strip_chromatic_frame의 채도 밴드
    탐지에 걸리지 않는다 — 순수 크롭/패딩/트림 동작만 검증하기 위함.
    """
    from PIL import Image, ImageDraw
    p = tmp_path / "page_000.png"
    img = Image.new("RGB", (w, h), (255, 255, 255))
    ImageDraw.Draw(img).rectangle(box, fill=color)
    img.save(p)
    return p


def test_image_블록_크롭(tmp_path):
    """Task 4: _crop_block이 bbox를 pad_out(6px)만큼 바깥으로 확장한 뒤
    strip_chromatic_frame(trim_uniform_margins 대신)으로 후처리하므로,
    bbox가 콘텐츠에 정확히 맞아떨어져도 결과에는 pad_out으로 확보된 실제
    배경 픽셀에서 온 pad=2 여백이 남는다(이전엔 bbox가 이미 타이트해
    트림이 더할 여백이 없어 원본 그대로 (100,200)이었음 — 이 갱신은
    pad_out 도입에 따른 의도된 변화).

    구 fg=(255,0,0)(고채도)은 strip_chromatic_frame이 콘텐츠 자체를
    "채도 프레임"으로 오인해 깎아낼 위험이 있어(단일 색으로 꽉 찬 크롭은
    전체가 채도 임계치를 넘음) 회색(채도 0)으로 바꿨다.
    """
    from PIL import Image
    page_png = _make_page_png_with_box(tmp_path, box=(50, 50, 149, 249))
    figures = tmp_path / "figures"
    pages = [{
        "index": 0,
        "dimensions": {},
        "markdown": "",
        "blocks": [{
            "type": "image",
            "top_left_x": 0.25, "top_left_y": 0.125,
            "bottom_right_x": 0.75, "bottom_right_y": 0.625,
            "content": "",
        }],
    }]
    layouts = build_layouts_from_ocr(pages, page_images=[page_png], figures_dir=figures)

    blk = layouts[0].blocks[0]
    assert blk.block_type is BlockType.FIGURE
    assert blk.image_path == "page_0000_blk_000.png"
    crop = Image.open(figures / blk.image_path)
    # 콘텐츠(100x200) + pad_out(6)로 확보된 실배경에서 pad=2씩 -> +4
    assert crop.size == (104, 204)
    cx, cy = crop.size[0] // 2, crop.size[1] // 2
    assert crop.getpixel((cx, cy)) == (128, 128, 128)  # 중심은 콘텐츠
    assert crop.getpixel((0, 0)) == (255, 255, 255)  # 모서리는 pad 배경


def test_bbox_바깥_pad_out으로_잘린_콘텐츠가_복원된다(tmp_path):
    """Mistral bbox가 다이어그램보다 4px씩 타이트해도(< pad_out=6),
    _crop_block이 bbox를 6px 바깥으로 확장해 크롭하므로 다이어그램
    가장자리가 잘리지 않고 최종 결과에 포함돼야 한다.
    """
    from PIL import Image
    # 다이어그램 실제 영역: (40,40)-(159,259) inclusive, 120x220
    page_png = _make_page_png_with_box(
        tmp_path, w=200, h=400, box=(40, 40, 159, 259), color=(128, 128, 128)
    )
    figures = tmp_path / "figures"
    # bbox는 다이어그램보다 4px씩 타이트 (44,44)-(156,256) — 픽셀 좌표 그대로 사용
    pages = [{
        "index": 0,
        "dimensions": {"width": 200, "height": 400},
        "markdown": "",
        "blocks": [{
            "type": "image",
            "top_left_x": 44, "top_left_y": 44,
            "bottom_right_x": 156, "bottom_right_y": 256,
            "content": "",
        }],
    }]
    layouts = build_layouts_from_ocr(pages, page_images=[page_png], figures_dir=figures)

    blk = layouts[0].blocks[0]
    crop = Image.open(figures / blk.image_path)
    # 다이어그램(120x220) + pad_out(6)-inset(4)=2px 실배경 여백 * 2변 = 124x224
    assert crop.size == (124, 224)
    # 다이어그램 우하단 모서리(구 타이트 bbox로는 잘렸을 픽셀)가 보존됐는지
    assert crop.getpixel((121, 221)) == (128, 128, 128)
    # 크롭 모서리는 배경(잘리지 않고 여백이 남아있음을 확인)
    assert crop.getpixel((0, 0)) == (255, 255, 255)


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


def test_bbox가_테두리를_못담으면_콘텐츠_경계까지_확장된다(tmp_path):
    """실물 발견: Mistral bbox가 다이어그램 자체의 하단/우측 테두리선을 몇 px
    못 담는 undershoot이 있음 → 크롭 경계에 콘텐츠가 걸려 있으면 배경이 나올
    때까지 확장해서 테두리를 온전히 포함해야 한다."""
    from PIL import Image, ImageDraw

    page = Image.new("RGB", (400, 400), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    draw.rectangle((50, 50, 349, 349), outline=(0, 0, 0), width=3)  # 표 바깥 테두리
    # 표 내부 선들 — bbox 경계에 stub으로 걸리는 실물 구조 재현
    for x in (150, 250):
        draw.line((x, 50, x, 349), fill=(0, 0, 0), width=3)  # 세로 칸막이
    draw.line((50, 200, 349, 200), fill=(0, 0, 0), width=3)  # 가로 칸막이
    p = tmp_path / "page.png"
    page.save(p)

    figures = tmp_path / "figures"
    pages = [{
        "index": 0, "dimensions": {"width": 400, "height": 400}, "markdown": "",
        # bbox가 테두리 안쪽(60..335)까지만 — 우측/하단 테두리(347~349)를 놓침
        "blocks": [{"type": "image", "top_left_x": 60, "top_left_y": 60,
                    "bottom_right_x": 335, "bottom_right_y": 335, "content": ""}],
    }]
    layouts = build_layouts_from_ocr(pages, page_images=[p], figures_dir=figures)
    blk = layouts[0].blocks[0]
    assert blk.image_path

    import numpy as np
    crop = np.asarray(Image.open(figures / blk.image_path).convert("RGB"), dtype=np.int16)
    dark = crop.max(axis=2) < 100
    # 하단/우측 테두리의 "긴 검은 런"이 크롭 안에 존재해야 한다 (200px 이상)
    assert max(int(dark[y].sum()) for y in range(dark.shape[0])) >= 280, "가로 테두리 미포함"
    assert max(int(dark[:, x].sum()) for x in range(dark.shape[1])) >= 280, "세로 테두리 미포함"
    # 그리고 크롭 마지막 행/열에 콘텐츠 stub이 걸려있지 않아야 한다 (잘림 흔적 없음)
    assert int(dark[-1].sum()) == 0 and int(dark[:, -1].sum()) == 0, "경계에 잘린 stub 존재"
