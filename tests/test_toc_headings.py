"""OCR 경로 heading → TOC 추출의 파편화 억제 테스트"""
from app.pipeline.layout import Block, BlockType, PageLayout
from app.pipeline.toc import _extract_from_headings


def _heading(text):
    return Block(block_type=BlockType.HEADING, bbox=(0, 0, 0, 0),
                 confidence=1.0, text=text)


def test_같은_페이지_두번째_이후_heading은_제외():
    layouts = [
        PageLayout(page_num=0, blocks=[_heading("1장 시작"), _heading("소제목 A"),
                                       _heading("소제목 B")]),
        PageLayout(page_num=3, blocks=[_heading("2장 시작"), _heading("소제목 C")]),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("1장 시작", 0), ("2장 시작", 3)]


def test_heading_없으면_빈_리스트():
    layouts = [PageLayout(page_num=0, blocks=[])]
    assert _extract_from_headings(layouts) == []
