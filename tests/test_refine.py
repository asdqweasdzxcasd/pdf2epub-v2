"""2-pass 작은 글씨(캡션·각주) 보정 테스트 (Mistral 호출은 전부 모킹)"""
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
from PIL import Image

from app.pipeline.layout import Block, BlockType, PageLayout
from app.pipeline.ocr_api import MistralOcrClient
from app.pipeline.refine import refine_small_text


class _FakeClient:
    """process_images 모킹용 페이크 클라이언트. 호출 인자를 기록한다."""

    def __init__(self, texts):
        self._texts = texts
        self.calls: list[list[Path]] = []

    def process_images(self, image_paths):
        self.calls.append(list(image_paths))
        return self._texts


def _make_page_png(tmp_path, name="page_0000.png", w=200, h=400) -> Path:
    p = tmp_path / name
    Image.new("RGB", (w, h), (255, 255, 255)).save(p)
    return p


def _caption_block(text="원본 캡션") -> Block:
    return Block(
        block_type=BlockType.CAPTION,
        bbox=(0.1, 0.1, 0.5, 0.2),
        confidence=1.0,
        text=text,
    )


def _footnote_block(text="원본 각주") -> Block:
    return Block(
        block_type=BlockType.FOOTNOTE,
        bbox=(0.1, 0.8, 0.9, 0.95),
        confidence=1.0,
        text=text,
    )


def test_캡션_텍스트가_교체된다(tmp_path):
    page_png = _make_page_png(tmp_path)
    layout = PageLayout(page_num=0, blocks=[_caption_block()])
    client = _FakeClient(["보정된 캡션"])

    n = refine_small_text([layout], [page_png], client, tmp_path)

    assert n == 1
    assert layout.blocks[0].text == "보정된 캡션"
    assert len(client.calls) == 1
    assert len(client.calls[0]) == 1  # 크롭 1건이 한 번의 process_images 호출로


def test_빈_응답이면_원본_유지(tmp_path):
    page_png = _make_page_png(tmp_path)
    block = _caption_block(text="원본 캡션")
    layout = PageLayout(page_num=0, blocks=[block])
    client = _FakeClient([""])  # 빈 문자열 응답

    n = refine_small_text([layout], [page_png], client, tmp_path)

    assert n == 0
    assert block.text == "원본 캡션"  # 원본 유지


def test_각주도_대상이다(tmp_path):
    page_png = _make_page_png(tmp_path)
    layout = PageLayout(page_num=0, blocks=[_footnote_block()])
    client = _FakeClient(["보정된 각주"])

    n = refine_small_text([layout], [page_png], client, tmp_path)

    assert n == 1
    assert layout.blocks[0].text == "보정된 각주"


def test_본문_PARAGRAPH는_건드리지_않는다(tmp_path):
    page_png = _make_page_png(tmp_path)
    paragraph = Block(
        block_type=BlockType.PARAGRAPH,
        bbox=(0.0, 0.3, 1.0, 0.7),
        confidence=1.0,
        text="본문 내용",
    )
    caption = _caption_block()
    layout = PageLayout(page_num=0, blocks=[paragraph, caption])
    client = _FakeClient(["보정된 캡션"])

    n = refine_small_text([layout], [page_png], client, tmp_path)

    assert n == 1
    assert paragraph.text == "본문 내용"  # 손대지 않음
    assert caption.text == "보정된 캡션"
    # process_images에 캡션 크롭 1건만 전달됨 (본문은 대상이 아님)
    assert len(client.calls[0]) == 1


def test_process_images가_단일_호출로_N페이지_PDF를_만든다(tmp_path):
    img_paths = [_make_page_png(tmp_path, name=f"c{i}.png", w=80, h=40) for i in range(3)]

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "pages": [
            {"index": 0, "markdown": "조각0"},
            {"index": 1, "markdown": "조각1"},
            {"index": 2, "markdown": "조각2"},
        ]
    }

    client = MistralOcrClient(api_key="k")
    with patch("app.pipeline.ocr_api.requests.post", return_value=resp) as mock_post:
        texts = client.process_images(img_paths)

    assert mock_post.call_count == 1  # 단일 호출
    assert texts == ["조각0", "조각1", "조각2"]

    # 실제로 3페이지짜리 PDF 하나가 업로드됐는지 payload를 직접 검증
    payload = mock_post.call_args.kwargs["json"]
    data_url = payload["document"]["document_url"]
    b64 = data_url.split(",", 1)[1]
    pdf_bytes = base64.b64decode(b64)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert doc.page_count == 3
    finally:
        doc.close()


def test_process_images_빈_리스트면_API_호출_없이_빈_리스트_반환():
    client = MistralOcrClient(api_key="k")
    with patch("app.pipeline.ocr_api.requests.post") as mock_post:
        texts = client.process_images([])
    assert texts == []
    mock_post.assert_not_called()


def test_bbox가_00으로_비어있으면_스킵(tmp_path):
    page_png = _make_page_png(tmp_path)
    block = Block(
        block_type=BlockType.CAPTION,
        bbox=(0.0, 0.0, 0.0, 0.0),
        confidence=1.0,
        text="원본",
    )
    layout = PageLayout(page_num=0, blocks=[block])
    client = _FakeClient(["보정됨"])

    n = refine_small_text([layout], [page_png], client, tmp_path)

    assert n == 0
    assert block.text == "원본"
    assert client.calls == []  # 대상이 없으니 process_images 자체가 호출 안 됨


def test_페이지_이미지_없으면_스킵(tmp_path):
    layout = PageLayout(page_num=0, blocks=[_caption_block()])
    client = _FakeClient(["보정됨"])

    n = refine_small_text([layout], [], client, tmp_path)  # page_images 비어있음

    assert n == 0
    assert client.calls == []
