"""run_pipeline OCR API 분기 테스트 (API는 모킹)"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.pipeline.run import _fill_missing_pages, run_pipeline


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
