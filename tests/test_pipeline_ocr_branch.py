"""run_pipeline OCR API 분기 테스트 (API는 모킹)"""
from pathlib import Path
from unittest.mock import patch

import fitz

from app.pipeline.progress import ProgressCallback
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
