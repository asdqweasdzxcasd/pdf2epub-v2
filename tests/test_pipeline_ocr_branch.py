"""run_pipeline OCR API 분기 테스트 (API는 모킹)"""
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

from app.pipeline.run import run_pipeline


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
