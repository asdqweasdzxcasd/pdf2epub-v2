"""헤딩 레벨 렌더링 + 본문 인라인 마크다운 → XHTML 변환 테스트"""
from app.pipeline.epub_build import _build_chapter_html, markdown_table_to_html
from app.pipeline.layout import Block, BlockType, PageLayout


def _block(block_type, text="", level=0):
    return Block(block_type=block_type, bbox=(0, 0, 0, 0), confidence=1.0,
                 text=text, level=level)


# --- heading 레벨별 태그 ---


def test_레벨0_헤딩은_h1로_렌더링된다_V1_호환():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.HEADING, "제목", level=0)])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>제목</h1>" in html


def test_레벨1_헤딩은_h1():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.HEADING, "1장", level=1)])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>1장</h1>" in html


def test_레벨2_헤딩은_h2():
    layout = PageLayout(
        page_num=0,
        blocks=[_block(BlockType.HEADING, "정적 자원과 브라우저 캐시", level=2)],
    )
    html = _build_chapter_html([layout], "장", {})
    assert "<h2>정적 자원과 브라우저 캐시</h2>" in html
    assert "<h1>" not in html


def test_레벨3_헤딩은_h3():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.HEADING, "소소제목", level=3)])
    html = _build_chapter_html([layout], "장", {})
    assert "<h3>소소제목</h3>" in html


# --- 짧은 제목(라틴 1~2자) 강등 없음 ---
#
# 예전에는 라틴 1~2자뿐인 heading을 OCR 잡음 라벨로 보고 <p>로 강등했으나,
# "AI", "US", "Go", "R", "C" 같은 정당한 짧은 제목까지 함께 강등되는 오탐이
# 있었다 (자세한 근거는 app/pipeline/epub_build.py의 _NOISE_HEADING_RE 자리
# 주석 참고). 강등 규칙 자체를 제거했으므로 길이와 무관하게 heading은 항상
# <h*>로 렌더링된다.


def test_라틴_1자_제목은_h1로_유지된다():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.HEADING, "B", level=1)])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>B</h1>" in html


def test_라틴_2자_제목도_h1로_유지된다():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.HEADING, "Ok", level=1)])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>Ok</h1>" in html


def test_라틴_3자_이상_제목은_그대로_heading_유지():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.HEADING, "Note", level=1)])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>Note</h1>" in html


def test_한글_1자_제목도_강등되지_않는다():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.HEADING, "기", level=1)])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>기</h1>" in html


# --- 본문 인라인 마크다운 -> XHTML ---


def test_본문_굵게_변환():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.PARAGRAPH, "이것은 **중요**합니다")])
    html = _build_chapter_html([layout], "장", {})
    assert "<strong>중요</strong>" in html
    assert "**" not in html


def test_각주_위첨자_변환():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.PARAGRAPH, "본문$^{5}$계속")])
    html = _build_chapter_html([layout], "장", {})
    assert "<sup>5</sup>" in html


def test_footnote_블록도_변환된다():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.FOOTNOTE, "각주 **강조** 내용")])
    html = _build_chapter_html([layout], "장", {})
    assert '<div class="footnote">' in html
    assert "<strong>강조</strong>" in html


def test_list_item도_변환된다():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.LIST_ITEM, "항목 **굵게**")])
    html = _build_chapter_html([layout], "장", {})
    assert "<strong>굵게</strong>" in html


def test_caption도_변환된다():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.CAPTION, "그림 1. **설명**")])
    html = _build_chapter_html([layout], "장", {})
    assert "<strong>설명</strong>" in html


def test_heading_텍스트의_마크다운도_변환된다():
    layout = PageLayout(
        page_num=0, blocks=[_block(BlockType.HEADING, "**중요** 제목", level=1)]
    )
    html = _build_chapter_html([layout], "장", {})
    assert "<strong>중요</strong> 제목" in html


def test_html_이스케이프는_여전히_적용된다():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.PARAGRAPH, "<script>1</script>")])
    html = _build_chapter_html([layout], "장", {})
    assert "&lt;script&gt;" in html
    assert "<script>1" not in html


# --- 표 셀도 to_xhtml 적용 ---


def test_표_셀에도_인라인_마크다운이_적용된다():
    md = "| 항목 | 값 |\n| --- | --- |\n| **중요** | 1 |"
    html = markdown_table_to_html(md)
    assert "<td><strong>중요</strong></td>" in html
