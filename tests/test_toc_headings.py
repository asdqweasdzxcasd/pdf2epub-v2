"""OCR 경로 heading → TOC 추출의 파편화 억제 테스트"""
from app.pipeline.layout import Block, BlockType, PageLayout
from app.pipeline.toc import _extract_from_headings


def _heading(text, level=1):
    return Block(block_type=BlockType.HEADING, bbox=(0, 0, 0, 0),
                 confidence=1.0, text=text, level=level)


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


def test_레벨2_헤딩은_목차에서_제외된다():
    """페이지당 첫 heading이라도 level 2 이상(소제목)이면 목차 항목으로
    채택하지 않는다 -- 소제목까지 챕터가 되는 파편화 방지."""
    layouts = [
        PageLayout(page_num=0, blocks=[_heading("1장", level=1)]),
        PageLayout(page_num=2, blocks=[_heading("1.1 소절", level=2)]),
        PageLayout(page_num=4, blocks=[_heading("2장", level=1)]),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("1장", 0), ("2장", 4)]


def test_레벨0_헤딩은_목차에_포함된다_V1_호환():
    layouts = [PageLayout(page_num=0, blocks=[_heading("V1 챕터", level=0)])]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("V1 챕터", 0)]


def test_toc_entry_level에_실제_레벨이_들어간다():
    layouts = [PageLayout(page_num=0, blocks=[_heading("1장", level=1)])]
    toc = _extract_from_headings(layouts)
    assert toc[0].level == 1
