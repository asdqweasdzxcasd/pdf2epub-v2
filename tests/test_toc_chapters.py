"""'N장' 구분 페이지 기준 챕터 경계 추출 테스트

실제 전공책은 heading 블록이 401개 있지만 진짜 장 구분은 "1장"~"11장" 형태의
구분 페이지 20개뿐이다. 나머지는 절 제목이거나 OCR이 잘못 title로 분류한
메모 박스 라벨이다. _extract_from_headings는 "N장" 단독 heading만 챕터
경계로 삼아야 하고, 그런 heading이 하나도 없는 문서에서는 기존 폴백(페이지당
첫 heading)으로 동작해야 한다.
"""
from app.pipeline.layout import Block, BlockType, PageLayout
from app.pipeline.toc import _extract_from_headings


def _heading(text, level=1):
    return Block(block_type=BlockType.HEADING, bbox=(0, 0, 0, 0),
                 confidence=1.0, text=text, level=level)


def _para(text):
    return Block(block_type=BlockType.PARAGRAPH, bbox=(0, 0, 0, 0),
                 confidence=1.0, text=text)


def test_장번호와_장이름이_이어붙어_하나의_목차항목이_된다():
    layouts = [
        PageLayout(page_num=0, blocks=[_heading("3장"), _heading("성능에 핵심인 DB")]),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("3장 성능에 핵심인 DB", 0)]


def test_다음_heading이_없으면_장번호만_사용():
    layouts = [
        PageLayout(page_num=0, blocks=[_heading("3장")]),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("3장", 0)]


def test_절제목은_목차에_들어가지_않는다():
    """장 구분(N장)이 문서 어딘가에 있으면 챕터 경계 모드로 동작하고,
    그 모드에서는 절 제목(N장 형식이 아닌 heading)이 있는 페이지는
    목차 항목을 만들지 않는다 -- 본문에는 그대로 <h*>로 남는다."""
    layouts = [
        PageLayout(page_num=0, blocks=[_heading("1장"), _heading("인덱스 기초")]),
        PageLayout(page_num=2, blocks=[_heading("커버링 인덱스 활용하기", level=2)]),
        PageLayout(page_num=4, blocks=[_heading("2장"), _heading("트랜잭션")]),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [
        ("1장 인덱스 기초", 0),
        ("2장 트랜잭션", 4),
    ]


def test_장구분_제목이_없으면_기존_폴백으로_동작():
    """'N장' 형태 heading이 문서에 하나도 없으면 페이지당 첫 heading을
    챕터 경계로 삼는 기존 폴백이 그대로 적용된다."""
    layouts = [
        PageLayout(page_num=0, blocks=[_heading("서론"), _heading("소제목 A")]),
        PageLayout(page_num=3, blocks=[_heading("본문 시작")]),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("서론", 0), ("본문 시작", 3)]


def test_한_페이지에_장구분이_여러개_몰려있으면_그_페이지는_무시():
    """목차 페이지에는 '1장','2장','3장' 같은 장 구분 heading이 한 페이지에
    몰려 나온다 -- 진짜 장 경계가 아니라 목차이므로 이 페이지 전체를
    무시하고, 다른 페이지의 진짜 장 구분만 채택한다."""
    layouts = [
        PageLayout(
            page_num=1,
            blocks=[_heading("1장"), _heading("2장"), _heading("3장")],
        ),
        PageLayout(page_num=10, blocks=[_heading("1장"), _heading("진짜 시작")]),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("1장 진짜 시작", 10)]


def test_장구분_제목의_마크다운_마커는_제거된다():
    layouts = [
        PageLayout(page_num=0, blocks=[_heading("3장"), _heading("**성능**에 핵심인 DB")]),
    ]
    toc = _extract_from_headings(layouts)
    assert toc[0].title == "3장 성능에 핵심인 DB"


def test_paragraph_블록은_next_heading_탐색에서_건너뛴다():
    """장 번호 heading 다음에 본문 paragraph가 먼저 오고 그 다음에 heading이
    와도, 그 heading을 장 이름으로 이어붙인다 (heading 블록만 본다)."""
    layouts = [
        PageLayout(
            page_num=0,
            blocks=[_heading("3장"), _para("어떤 부제 설명"), _heading("성능에 핵심인 DB")],
        ),
    ]
    toc = _extract_from_headings(layouts)
    assert [(e.title, e.page_num) for e in toc] == [("3장 성능에 핵심인 DB", 0)]
