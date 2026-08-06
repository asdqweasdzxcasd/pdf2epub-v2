"""run_pipeline OCR API 분기 테스트 (API는 모킹)"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.pipeline.layout import Block, BlockType, PageLayout
from app.pipeline.run import (
    _fill_missing_pages,
    _replace_design_pages,
    _replace_front_matter_design_pages,
    run_pipeline,
)


class _NullProgress:
    def update(self, pct, stage, msg=""):
        pass


def _make_image_pdf(tmp_path) -> Path:
    """텍스트 없는 이미지 PDF (ocr_needed=True 유도)"""
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(20, 20, 180, 280))
    shape.finish(fill=(0.5, 0.5, 0.5))
    shape.commit()
    p = tmp_path / "img.pdf"
    doc.save(p)
    return p


def _fake_pages(n):
    return [
        {"index": i, "dimensions": {}, "markdown": "",
         "blocks": [{"type": "text", "top_left_x": 0.1, "top_left_y": 0.1,
                     "bottom_right_x": 0.9, "bottom_right_y": 0.3,
                     "content": f"본문 {i}"}]}
        for i in range(n)
    ]


def test_ocr_mode_api_분기_호출(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        result = run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="api",
        )
    assert out.exists()
    assert result.page_count == 1
    MockClient.return_value.process_pdf.assert_called_once()


def test_ocr_mode_off는_V1_경로(tmp_path):
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="off",
        )
    assert out.exists()
    MockClient.assert_not_called()  # V1 fallback (페이지 PNG 임베드)


def test_auto는_키_없으면_V1_경로(tmp_path, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="auto",
        )
    assert out.exists()
    MockClient.assert_not_called()


def test_auto는_이미지PDF_키있으면_API_사용(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="auto",
        )
    assert out.exists()
    MockClient.assert_called_once()
    # OCR_MODEL 설정이 클라이언트에 전달되는지 (finding 1 회귀 방지)
    assert MockClient.call_args.kwargs.get("model")


def test_빈_블록_페이지는_페이지_이미지로_보정(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    empty_pages = [{"index": 0, "dimensions": {}, "markdown": "", "blocks": []}]
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = empty_pages
        run_pipeline(pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
                     ocr_mode="api")
    import zipfile
    names = zipfile.ZipFile(out).namelist()
    assert any(n.endswith(".png") for n in names), "빈 페이지가 이미지로 보정되지 않음"


def test_trim_기본값이면_트림된_PDF가_클라이언트에_전달(tmp_path, monkeypatch):
    """trim=True(기본)이면 원본이 아니라 여백을 트림한 이미지-only PDF가
    OCR API로 전달돼야 한다 — 좌표계 일치(Mistral이 본 이미지 = bbox 기준 =
    크롭 소스)가 핵심 계약이다.
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    temp_dir = tmp_path / "t"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        run_pipeline(
            pdf, out, temp_dir, "cpu", "테스트", _NullProgress(),
            ocr_mode="api",
        )

    called_path = Path(MockClient.return_value.process_pdf.call_args.args[0])
    assert called_path != pdf
    assert called_path.name == "trimmed.pdf"
    assert called_path.exists()

    # 트림된 PDF의 페이지 이미지가 원본 렌더 이미지보다 작아야 한다(여백 제거)
    orig_size = Image.open(temp_dir / "pages" / "0000.png").size
    trimmed_size = Image.open(temp_dir / "trimmed" / "page_0000.png").size
    assert trimmed_size[0] < orig_size[0]
    assert trimmed_size[1] < orig_size[1]


def test_no_trim이면_원본_PDF가_그대로_전달(tmp_path, monkeypatch):
    """--no-trim 상당(trim=False)이면 트림 없이 원본 pdf_path가 그대로 전달된다."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="api", trim=False,
        )

    called_path = Path(MockClient.return_value.process_pdf.call_args.args[0])
    assert called_path == pdf
    assert not (tmp_path / "t" / "trimmed.pdf").exists()


def test_응답에서_누락된_페이지도_보정(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_image_pdf(tmp_path)  # 1페이지 PDF
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = []  # 페이지 통째 누락
        run_pipeline(pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
                     ocr_mode="api")
    import zipfile
    names = zipfile.ZipFile(out).namelist()
    assert any(n.endswith(".png") for n in names)


def test_트림된_PDF_페이지_크기가_포인트_단위로_선언됨(tmp_path, monkeypatch):
    """트림된 PDF의 페이지 크기가 픽셀이 아니라 올바른 포인트 단위로
    선언되었는지 검증한다. 픽셀을 그대로 포인트로 쓰면 엄청 큰 페이지가 된다.

    예: 1000×1500 픽셀 @ 300 DPI
    - 잘못된 경우: page_width=1000pt ≈ 14인치 (💥 너무 큼)
    - 올바른 경우: page_width=1000×72/300 ≈ 240pt ≈ 3.3인치
    """
    from app.pipeline.pdf_render import RENDER_DPI
    from app.pipeline.run import _prepare_trimmed_input

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    temp_dir = tmp_path / "t"
    temp_dir.mkdir()

    # 테스트용 이미지 생성: 1000×1500 픽셀 @ 300 DPI (렌더 해상도 시뮬레이션)
    img = Image.new("RGB", (1000, 1500), color="white")
    img_path = tmp_path / "test.png"
    img.save(img_path)

    # _prepare_trimmed_input 호출
    trimmed_pdf_path, trimmed_paths = _prepare_trimmed_input([img_path], temp_dir)

    # 트림된 PDF를 열어서 실제 페이지 크기 검증
    doc = fitz.open(str(trimmed_pdf_path))
    try:
        page = doc[0]
        rect = page.rect
        pt_w = rect.width
        pt_h = rect.height

        # 예상 포인트 크기 (여백 제거 후 약간 작아질 수 있으므로 여유 범위 사용)
        expected_pt_w = 1000 * 72 / RENDER_DPI
        expected_pt_h = 1500 * 72 / RENDER_DPI

        # ±5pt 오차 범위 (트림 때문에 약간 작아질 수 있음)
        assert abs(pt_w - expected_pt_w) < 150, \
            f"Width mismatch: got {pt_w:.1f}pt, expected ~{expected_pt_w:.1f}pt"
        assert abs(pt_h - expected_pt_h) < 150, \
            f"Height mismatch: got {pt_h:.1f}pt, expected ~{expected_pt_h:.1f}pt"

        # 픽셀 단위로 선언됐다면 이정도 되었을 것 (문제 없음 확인)
        assert pt_w < 500, f"Page width {pt_w}pt seems to be in pixels, not points"
        assert pt_h < 700, f"Page height {pt_h}pt seems to be in pixels, not points"
    finally:
        doc.close()


def test_트림된_PDF에는_JPEG로_임베드되어_업로드_크기가_줄어든다(tmp_path):
    """finding 3: 업로드용 PDF에는 트림 이미지를 JPEG(quality=88)로
    임베드해 크기를 절감해야 한다. 단, 크롭 소스(trimmed_paths)는 여전히
    무손실 PNG여야 하고 픽셀 치수는 동일해야 한다(좌표 정합 유지).
    """
    from app.pipeline.run import _prepare_trimmed_input

    temp_dir = tmp_path / "t"
    temp_dir.mkdir()

    # 사진풍 노이즈 이미지 (PNG는 잘 압축되지 않고 JPEG는 잘 압축되는 케이스)
    rng = np.random.default_rng(42)
    noisy = rng.integers(0, 256, size=(600, 800, 3), dtype=np.uint8)
    photo = Image.fromarray(noisy, mode="RGB").filter(ImageFilter.GaussianBlur(radius=2))
    img_path = tmp_path / "photo.png"
    photo.save(img_path)

    trimmed_pdf_path, trimmed_paths = _prepare_trimmed_input([img_path], temp_dir)

    # 크롭 소스는 여전히 무손실 PNG
    assert trimmed_paths[0].suffix == ".png"
    png_size_bytes = trimmed_paths[0].stat().st_size
    png_pixel_size = Image.open(trimmed_paths[0]).size

    doc = fitz.open(str(trimmed_pdf_path))
    try:
        img_list = doc[0].get_images(full=True)
        assert img_list, "임베드된 이미지가 없음"
        xref = img_list[0][0]
        embedded = doc.extract_image(xref)

        assert embedded["ext"] in ("jpeg", "jpg"), \
            f"업로드 PDF 이미지가 JPEG로 인코딩되지 않음: ext={embedded['ext']}"
        assert len(embedded["image"]) < png_size_bytes, \
            "JPEG 임베드가 PNG 대비 작아야 한다"

        # 픽셀 치수(좌표 정합의 기준)는 PNG 크롭 소스와 동일해야 한다
        pix = fitz.Pixmap(doc, xref)
        assert (pix.width, pix.height) == png_pixel_size
    finally:
        doc.close()


def _make_blank_page_png(tmp_path, name="blank.png", size=(200, 300), color=(255, 255, 255)):
    p = tmp_path / name
    Image.new("RGB", size, color).save(p)
    return p


def _make_photo_page_png(tmp_path, name="photo.png", size=(200, 300)):
    """그림 한 장이 있는 페이지 (단색 아님) — 챕터 백지와 구분되어야 한다."""
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 20, 180, 180), fill=(200, 50, 30))
    draw.rectangle((40, 200, 160, 280), fill=(30, 120, 200))
    p = tmp_path / name
    img.save(p)
    return p


def test_단색_백지_페이지는_레이아웃에서_제외된다(tmp_path):
    """OCR 블록이 0개로 온 챕터 구분용 백지 페이지는 무의미한 이미지
    임베드를 피하기 위해 결과 레이아웃에서 통째로 제외돼야 한다 (빈
    PageLayout도 넣지 않음)."""
    blank_png = _make_blank_page_png(tmp_path)
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=1, page_images=[blank_png])

    result = _fill_missing_pages([], render_result, figures_dir)

    assert result == []
    assert list(figures_dir.iterdir()) == []


def test_그림_페이지는_단색이_아니므로_유지된다(tmp_path):
    """그림 한 장만 있는 페이지(사진 전면 페이지)는 단색이 아니므로 영향
    없이 기존처럼 페이지 이미지로 보정돼야 한다."""
    photo_png = _make_photo_page_png(tmp_path)
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=1, page_images=[photo_png])

    result = _fill_missing_pages([], render_result, figures_dir)

    assert len(result) == 1
    assert result[0].blocks and result[0].blocks[0].image_path


def test_일부만_단색인_경우_단색_페이지만_제외된다(tmp_path):
    blank_png = _make_blank_page_png(tmp_path, "blank.png")
    photo_png = _make_photo_page_png(tmp_path, "photo.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=2, page_images=[blank_png, photo_png])

    result = _fill_missing_pages([], render_result, figures_dir)

    assert [layout.page_num for layout in result] == [1]


def _make_cover_pdf(tmp_path, name="cover.pdf") -> Path:
    """비백색 비율이 높은(표지처럼 디자인된) 1페이지 PDF.

    두 가지 대비되는 색 블록으로 페이지 대부분을 채운다 -- 단색 한 가지로만
    채우면 is_blank_page(표준편차 낮음)에 걸려 '단색 백지'로 오인되므로,
    대비되는 두 색을 써서 표준편차를 확보한다.
    """
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(0, 0, 200, 150))
    shape.finish(fill=(0.1, 0.2, 0.5))
    shape.commit()
    shape2 = page.new_shape()
    shape2.draw_rect(fitz.Rect(0, 150, 200, 300))
    shape2.finish(fill=(0.85, 0.75, 0.1))
    shape2.commit()
    p = tmp_path / name
    doc.save(p)
    return p


def _make_sparse_image_pdf(tmp_path, name="sparse.pdf") -> Path:
    """텍스트는 없지만(ocr_needed=True 유도) 비백색 비율이 낮은 1페이지 PDF
    -- 일반 본문 첫 페이지(표지 아님)를 흉내낸다."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(20, 20, 60, 50))
    shape.finish(fill=(0.3, 0.3, 0.3))
    shape.commit()
    p = tmp_path / name
    doc.save(p)
    return p


def test_디자인된_표지는_이미지_블록_하나로_대체되고_커버_메타데이터가_등록된다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_cover_pdf(tmp_path)
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        result = run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="api",
        )
    assert result.page_count == 1

    import zipfile
    zf = zipfile.ZipFile(out)
    names = zf.namelist()

    xhtml_name = next(n for n in names if n.endswith(".xhtml") and "chapter" in n)
    chapter_html = zf.read(xhtml_name).decode("utf-8")
    # OCR이 준 텍스트("본문 0") 대신 페이지 이미지 하나로 대체돼야 한다
    assert "본문 0" not in chapter_html
    assert "<img" in chapter_html

    opf_name = next(n for n in names if n.endswith(".opf"))
    opf = zf.read(opf_name).decode("utf-8")
    assert 'name="cover"' in opf


def test_일반_텍스트_첫페이지는_표지로_오인되지_않는다(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_sparse_image_pdf(tmp_path)
    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="api",
        )

    import zipfile
    zf = zipfile.ZipFile(out)
    names = zf.namelist()
    xhtml_name = next(n for n in names if n.endswith(".xhtml") and "chapter" in n)
    chapter_html = zf.read(xhtml_name).decode("utf-8")
    # 표지로 오인되지 않았으므로 기존대로 OCR 텍스트가 그대로 남아있어야 한다
    assert "본문 0" in chapter_html

    opf_name = next(n for n in names if n.endswith(".opf"))
    opf = zf.read(opf_name).decode("utf-8")
    assert 'name="cover"' not in opf


def test_단색_표지_페이지는_표지로_오인되지_않는다(tmp_path, monkeypatch):
    """is_blank_page(단색)인 첫 페이지는 ink_coverage가 높아도 표지로
    오인해선 안 된다(챕터 구분용 단색 페이지 등)."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(0, 0, 200, 300))
    shape.finish(fill=(0.2, 0.2, 0.2))
    shape.commit()
    pdf = tmp_path / "blank_dark.pdf"
    doc.save(pdf)

    out = tmp_path / "out.epub"
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="api",
        )

    import zipfile
    zf = zipfile.ZipFile(out)
    opf_name = next(n for n in zf.namelist() if n.endswith(".opf"))
    opf = zf.read(opf_name).decode("utf-8")
    assert 'name="cover"' not in opf


def test_refine_실패가_전체_변환을_죽이지_않는다(tmp_path, monkeypatch):
    """Finding 1: refine_small_text가 OcrApiError를 던져도 변환이 계속되고
    EPUB이 생성된다. 보정은 부가 기능이므로 실패해도 무시한다.
    """
    from app.pipeline.ocr_api import OcrApiError

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_image_pdf(tmp_path)
    out = tmp_path / "out.epub"

    def side_effect(*args, **kwargs):
        raise OcrApiError("mock refine 실패")

    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = _fake_pages(1)
        # refine_small_text를 patch해서 OcrApiError 발생
        with patch("app.pipeline.run.refine_small_text", side_effect=side_effect):
            # 예외가 전파되지 않고 EPUB이 생성되어야 한다
            result = run_pipeline(
                pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
                ocr_mode="api",
                refine=True,
            )

    assert out.exists(), "refine 실패해도 EPUB이 생성되어야 한다"
    assert result.page_count == 1


# --- _replace_front_matter_design_pages ---


def _heading_block(text, level=1):
    return Block(block_type=BlockType.HEADING, bbox=(0, 0, 0, 0),
                 confidence=1.0, text=text, level=level)


def _para_block(text):
    return Block(block_type=BlockType.PARAGRAPH, bbox=(0, 0, 0, 0),
                 confidence=1.0, text=text)


def _make_design_page_png(tmp_path, name="design.png", size=(200, 300)):
    """컬러 챕터 밴드가 넓은 디자인 페이지 (차례류)를 흉내낸다."""
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).rectangle((0, 0, size[0] - 1, 90), fill=(230, 90, 60))
    p = tmp_path / name
    img.save(p)
    return p


def _make_text_page_png(tmp_path, name="text.png", size=(200, 300)):
    """유채색 비율이 낮은 본문 텍스트 페이지(머리말 등)를 흉내낸다."""
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for y in range(20, 280, 15):
        draw.rectangle((20, y, 180, y + 8), fill=(20, 20, 20))
    p = tmp_path / name
    img.save(p)
    return p


def test_장구분_앞_유채색_페이지는_FIGURE로_대체된다(tmp_path):
    design_png = _make_design_page_png(tmp_path, "p0.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=2, page_images=[design_png, None])

    page_layouts = [
        PageLayout(page_num=0, blocks=[_para_block("차례"), _para_block("1장 ...")]),
        PageLayout(page_num=1, blocks=[_heading_block("1장"), _heading_block("시작")]),
    ]

    result = _replace_front_matter_design_pages(page_layouts, render_result, figures_dir)

    page0 = next(pl for pl in result if pl.page_num == 0)
    assert len(page0.blocks) == 1
    assert page0.blocks[0].block_type == BlockType.FIGURE
    assert page0.blocks[0].image_path
    # 장 구분 페이지(1) 자체는 건드리지 않는다
    page1 = next(pl for pl in result if pl.page_num == 1)
    assert page1.blocks[0].block_type == BlockType.HEADING


def test_유채색_낮은_판권_페이지도_FIGURE로_대체된다(tmp_path):
    """갱신 사유: 판권/서지 정보 페이지는 인쇄 기준 7~8pt의 아주 작은 글씨라
    Mistral OCR의 내부 정규화(~1020px)로 뭉개져 오타가 다발한다(실측:
    "최범균"→"최법균" 등). 예전엔 chroma_coverage(유채색 비율) 조건 때문에
    저채도(무채색에 가까운) 이런 페이지가 텍스트로 유지됐지만, 텍스트 재조판이
    아니라 이미지 보존이 맞는 처리이므로 이제는 장 구분 페이지 이전이면
    유채색 비율과 무관하게 FIGURE로 대체돼야 한다."""
    text_png = _make_text_page_png(tmp_path, "p0.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=2, page_images=[text_png, None])

    page_layouts = [
        PageLayout(page_num=0, blocks=[_para_block("머리말 내용")]),
        PageLayout(page_num=1, blocks=[_heading_block("1장"), _heading_block("시작")]),
    ]

    result = _replace_front_matter_design_pages(page_layouts, render_result, figures_dir)

    page0 = next(pl for pl in result if pl.page_num == 0)
    assert len(page0.blocks) == 1
    assert page0.blocks[0].block_type == BlockType.FIGURE
    assert page0.blocks[0].image_path
    # 장 구분 페이지(1) 자체는 건드리지 않는다
    page1 = next(pl for pl in result if pl.page_num == 1)
    assert page1.blocks[0].block_type == BlockType.HEADING


def test_단색_백지_앞페이지는_대체되지_않는다(tmp_path):
    """단색 백지(챕터 구분용)는 판권 페이지가 아니므로 이 함수의 대체 대상이
    아니다 -- 무의미한 이미지 임베드를 막기 위해 _fill_missing_pages가 별도로
    통째 제외를 담당하므로, 여기서는 손대지 않고 원래 레이아웃을 그대로
    유지해야 한다."""
    blank_png = _make_blank_page_png(tmp_path, "p0.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=2, page_images=[blank_png, None])

    page_layouts = [
        PageLayout(page_num=0, blocks=[]),
        PageLayout(page_num=1, blocks=[_heading_block("1장"), _heading_block("시작")]),
    ]

    result = _replace_front_matter_design_pages(page_layouts, render_result, figures_dir)

    page0 = next(pl for pl in result if pl.page_num == 0)
    assert page0.blocks == []  # 대체되지 않고 원래(빈) 레이아웃 유지


def test_장구분_뒤의_그림_페이지는_영향받지_않는다(tmp_path):
    """장 구분 이후 페이지는 유채색 비율이 높아도(그림 있는 본문 페이지)
    이 함수가 건드리면 안 된다 -- 위치 조건이 유채색 비율 오탐을 막는다."""
    photo_png = _make_photo_page_png(tmp_path, "p2.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=3, page_images=[None, None, photo_png])

    page_layouts = [
        PageLayout(page_num=0, blocks=[_para_block("머리말")]),
        PageLayout(page_num=1, blocks=[_heading_block("1장"), _heading_block("시작")]),
        PageLayout(page_num=2, blocks=[_para_block("본문 설명")]),
    ]

    result = _replace_front_matter_design_pages(page_layouts, render_result, figures_dir)

    page2 = next(pl for pl in result if pl.page_num == 2)
    assert page2.blocks[0].block_type == BlockType.PARAGRAPH
    assert page2.blocks[0].text == "본문 설명"


def test_장구분_없는_문서는_전부_기존대로_유지된다(tmp_path):
    """"N장" 형태 장 구분을 찾지 못하면(다른 책 형식) 이 처리를 하지 않고
    page_layouts를 그대로 반환해야 한다."""
    design_png = _make_design_page_png(tmp_path, "p0.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=1, page_images=[design_png])

    page_layouts = [
        PageLayout(page_num=0, blocks=[_para_block("차례처럼 보이지만 장구분 heading 없음")]),
    ]

    result = _replace_front_matter_design_pages(page_layouts, render_result, figures_dir)

    assert result == page_layouts
    assert result[0].blocks[0].block_type == BlockType.PARAGRAPH


# --- _replace_design_pages (전면 컬러 디자인 페이지: 표지 + 장 구분 페이지 등) ---


def _make_full_color_page_png(tmp_path, name="fullcolor.png", size=(200, 300)):
    """전면 컬러 디자인 페이지(장 구분 페이지 등)를 흉내낸다 -- ink_coverage가
    높고(잉크 비율 ~1.0) 단색이 아니다(대비되는 두 색이라 is_blank_page 미해당)."""
    img = Image.new("RGB", size, (40, 60, 160))
    ImageDraw.Draw(img).rectangle((0, size[1] // 2, size[0], size[1]), fill=(220, 200, 40))
    p = tmp_path / name
    img.save(p)
    return p


def test_고잉크_페이지는_FIGURE_하나로_대체되고_page_num_보존(tmp_path):
    """잉크 비율 0.5 이상인 페이지(합성: 전면 색 채운 이미지 + 텍스트 블록)는
    FIGURE 블록 1개로 대체되고 page_num이 보존돼야 한다."""
    design_png = _make_full_color_page_png(tmp_path, "p3.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(
        page_count=4, page_images=[None, None, None, design_png]
    )

    page_layouts = [
        PageLayout(
            page_num=3,
            blocks=[
                _heading_block("3장"),
                _heading_block("성능을 좌우하는 DB 설계와 쿼리"),
            ],
        ),
    ]

    result = _replace_design_pages(page_layouts, render_result, figures_dir)

    assert len(result) == 1
    page = result[0]
    assert page.page_num == 3  # page_num 보존 (챕터 분할이 이 값에 의존)
    assert len(page.blocks) == 1
    assert page.blocks[0].block_type == BlockType.FIGURE
    assert page.blocks[0].image_path


def test_저잉크_본문_페이지는_텍스트_유지(tmp_path):
    """잉크 비율이 낮은 본문 텍스트 페이지는 그대로 텍스트로 남아야 한다."""
    text_png = _make_text_page_png(tmp_path, "body.png")
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=1, page_images=[text_png])

    page_layouts = [PageLayout(page_num=0, blocks=[_para_block("본문 내용")])]

    result = _replace_design_pages(page_layouts, render_result, figures_dir)

    assert result[0].blocks[0].block_type == BlockType.PARAGRAPH
    assert result[0].blocks[0].text == "본문 내용"


def test_단색_백지는_디자인_페이지로_대체되지_않는다(tmp_path):
    """is_blank_page(단색)인 페이지는 잉크 비율이 낮아 애초에 이 규칙 대상이
    아니지만(단색은 ink_coverage도 보통 0에 가까움), 어두운 단색 백지처럼
    ink_coverage가 높아도 is_blank_page면 대체되면 안 된다는 것을 확인한다."""
    dark_blank = tmp_path / "dark_blank.png"
    Image.new("RGB", (200, 300), (20, 20, 20)).save(dark_blank)
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=1, page_images=[dark_blank])

    page_layouts = [PageLayout(page_num=0, blocks=[])]

    result = _replace_design_pages(page_layouts, render_result, figures_dir)

    assert result[0].blocks == []  # 대체되지 않고 원래(빈) 레이아웃 유지


def test_이미_FIGURE인_페이지는_중복_대체하지_않는다(tmp_path):
    """표지/앞부분 디자인 페이지 대체로 이미 FIGURE 블록 하나뿐인 페이지는
    이 규칙이 다시 건드리면 안 된다(중복 임베드 방지)."""
    design_png = _make_full_color_page_png(tmp_path)
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    render_result = SimpleNamespace(page_count=1, page_images=[design_png])
    existing_fig = Block(
        block_type=BlockType.FIGURE, bbox=(0, 0, 1, 1), confidence=1.0,
        image_path="existing.png",
    )
    page_layouts = [PageLayout(page_num=0, blocks=[existing_fig])]

    result = _replace_design_pages(page_layouts, render_result, figures_dir)

    assert result[0].blocks[0].image_path == "existing.png"


def _make_chapter_divider_pdf(tmp_path, name="chapter_divider.pdf") -> Path:
    """2페이지 PDF: 0페이지는 저잉크 본문(텍스트 없음, ocr_needed 유도),
    1페이지는 전면 컬러 장 구분 페이지(고잉크)를 흉내낸다."""
    doc = fitz.open()

    body_page = doc.new_page(width=200, height=300)
    shape = body_page.new_shape()
    shape.draw_rect(fitz.Rect(20, 20, 60, 50))
    shape.finish(fill=(0.3, 0.3, 0.3))
    shape.commit()

    divider_page = doc.new_page(width=200, height=300)
    shape2 = divider_page.new_shape()
    shape2.draw_rect(fitz.Rect(0, 0, 200, 150))
    shape2.finish(fill=(0.15, 0.25, 0.6))
    shape2.commit()
    shape3 = divider_page.new_shape()
    shape3.draw_rect(fitz.Rect(0, 150, 200, 300))
    shape3.finish(fill=(0.85, 0.8, 0.15))
    shape3.commit()

    p = tmp_path / name
    doc.save(p)
    return p


def test_장구분_페이지가_이미지로_바뀌어도_목차는_그대로_보존된다(tmp_path, monkeypatch):
    """run_pipeline 통합 테스트: 장 구분 제목("3장" + 장 이름)이 있는 고잉크
    페이지가 이미지로 대체돼도, extract_toc가 먼저 실행되므로 목차 항목은
    그대로 살아남아야 한다. 최종 EPUB의 챕터 파일도 페이지 이미지로
    렌더돼야 한다(텍스트가 아니라 <img>)."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    pdf = _make_chapter_divider_pdf(tmp_path)
    out = tmp_path / "out.epub"
    fake_pages = [
        {
            "index": 0,
            "dimensions": {},
            "markdown": "",
            "blocks": [
                {"type": "text", "top_left_x": 0.1, "top_left_y": 0.1,
                 "bottom_right_x": 0.9, "bottom_right_y": 0.3,
                 "content": "머리말 본문"},
            ],
        },
        {
            "index": 1,
            "dimensions": {},
            "markdown": "",
            "blocks": [
                {"type": "title", "top_left_x": 0.3, "top_left_y": 0.1,
                 "bottom_right_x": 0.7, "bottom_right_y": 0.2,
                 "content": "3장"},
                {"type": "title", "top_left_x": 0.1, "top_left_y": 0.3,
                 "bottom_right_x": 0.9, "bottom_right_y": 0.4,
                 "content": "성능을 좌우하는 DB 설계와 쿼리"},
            ],
        },
    ]
    with patch("app.pipeline.run.MistralOcrClient") as MockClient:
        MockClient.return_value.process_pdf.return_value = fake_pages
        result = run_pipeline(
            pdf, out, tmp_path / "t", "cpu", "테스트", _NullProgress(),
            ocr_mode="api",
        )

    # 목차 항목이 그대로 보존됨 (extract_toc가 이미지 대체보다 먼저 실행됨)
    assert result.toc_count == 1

    import zipfile
    zf = zipfile.ZipFile(out)
    names = zf.namelist()

    # 장 구분 페이지가 담긴 챕터 파일은 텍스트 대신 이미지로 렌더돼야 한다
    # (page_num 보존 덕분에 챕터 분할은 여전히 정상 동작).
    divider_html = None
    for n in sorted(nm for nm in names if nm.endswith(".xhtml") and "chapter" in nm):
        html = zf.read(n).decode("utf-8")
        if "<img" in html and "머리말 본문" not in html:
            divider_html = html
            break
    assert divider_html is not None, "장 구분 페이지가 이미지로 렌더된 챕터를 찾지 못함"
    # <title> 태그(=TOC 제목, 메타데이터)엔 남아있는 게 정상이므로 본문
    # (<body> 이후)에만 텍스트가 없는지 확인한다.
    divider_body = divider_html.split("<body>", 1)[1]
    assert "3장" not in divider_body
    assert "성능을 좌우하는 DB 설계와 쿼리" not in divider_body

    # nav(목차)에는 원래 제목이 그대로 남아있어야 한다
    nav_name = next(n for n in names if n.endswith("nav.xhtml"))
    nav_html = zf.read(nav_name).decode("utf-8")
    assert "3장" in nav_html
    assert "성능을 좌우하는 DB 설계와 쿼리" in nav_html
