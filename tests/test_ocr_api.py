"""Mistral OCR 클라이언트 테스트 (HTTP는 전부 모킹)"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
import pytest
import requests

from app.pipeline.ocr_api import MistralOcrClient, OcrApiError


def _make_pdf(tmp_path, pages=3) -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=300)
        page.insert_text((50, 50), f"page {i}")
    p = tmp_path / "t.pdf"
    doc.save(p)
    return p


def _ok_response(page_indices):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "pages": [{"index": i, "blocks": [], "markdown": "", "dimensions": {}}
                  for i in page_indices]
    }
    return resp


def test_단일_청크_호출(tmp_path):
    pdf = _make_pdf(tmp_path, pages=3)
    client = MistralOcrClient(api_key="k", chunk_size=40)
    with patch("app.pipeline.ocr_api.requests.post",
               return_value=_ok_response([0, 1, 2])) as mock_post:
        pages = client.process_pdf(pdf)
    assert mock_post.call_count == 1
    assert [p["index"] for p in pages] == [0, 1, 2]
    # 키가 헤더로 전달됐는지
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer k"


def test_청크_분할과_절대_인덱스_재작성(tmp_path):
    pdf = _make_pdf(tmp_path, pages=5)
    client = MistralOcrClient(api_key="k", chunk_size=2)
    # 각 청크 응답은 청크 내 상대 index (0부터)
    responses = [_ok_response([0, 1]), _ok_response([0, 1]), _ok_response([0])]
    with patch("app.pipeline.ocr_api.requests.post", side_effect=responses) as mock_post:
        pages = client.process_pdf(pdf)
    assert mock_post.call_count == 3
    assert [p["index"] for p in pages] == [0, 1, 2, 3, 4]  # 절대 번호로 재작성


def test_429_재시도_후_성공(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    rate_limited = MagicMock(status_code=429, text="rate limit")
    client = MistralOcrClient(api_key="k", max_retries=3)
    with patch("app.pipeline.ocr_api.requests.post",
               side_effect=[rate_limited, _ok_response([0])]), \
         patch("app.pipeline.ocr_api.time.sleep") as mock_sleep:
        pages = client.process_pdf(pdf)
    assert len(pages) == 1
    mock_sleep.assert_called()  # 재시도 전 대기했는지


def test_재시도_소진시_예외(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    err = MagicMock(status_code=500, text="server error")
    client = MistralOcrClient(api_key="k", max_retries=2)
    with patch("app.pipeline.ocr_api.requests.post", side_effect=[err, err]), \
         patch("app.pipeline.ocr_api.time.sleep"):
        with pytest.raises(OcrApiError) as e:
            client.process_pdf(pdf)
    assert "500" in str(e.value)


def test_4xx는_즉시_실패(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    err = MagicMock(status_code=401, text="unauthorized")
    client = MistralOcrClient(api_key="bad", max_retries=3)
    with patch("app.pipeline.ocr_api.requests.post", return_value=err) as mock_post:
        with pytest.raises(OcrApiError):
            client.process_pdf(pdf)
    assert mock_post.call_count == 1  # 인증 오류는 재시도 무의미


def test_네트워크_예외_재시도_후_성공(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    client = MistralOcrClient(api_key="k", max_retries=3)
    with patch("app.pipeline.ocr_api.requests.post",
               side_effect=[requests.ConnectionError("boom"), _ok_response([0])]), \
         patch("app.pipeline.ocr_api.time.sleep") as mock_sleep:
        pages = client.process_pdf(pdf)
    assert len(pages) == 1
    mock_sleep.assert_called()


def test_네트워크_예외_소진시_OcrApiError(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    client = MistralOcrClient(api_key="k", max_retries=2)
    with patch("app.pipeline.ocr_api.requests.post",
               side_effect=requests.Timeout("t")), \
         patch("app.pipeline.ocr_api.time.sleep"):
        with pytest.raises(OcrApiError) as e:
            client.process_pdf(pdf)
    assert "Timeout" in str(e.value)


def test_200이지만_본문이_JSON아니면_재시도(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    bad = MagicMock(status_code=200)
    bad.json.side_effect = ValueError("not json")
    client = MistralOcrClient(api_key="k", max_retries=2)
    with patch("app.pipeline.ocr_api.requests.post",
               side_effect=[bad, _ok_response([0])]), \
         patch("app.pipeline.ocr_api.time.sleep"):
        pages = client.process_pdf(pdf)
    assert len(pages) == 1
