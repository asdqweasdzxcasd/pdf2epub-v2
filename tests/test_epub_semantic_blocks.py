"""목록/코드/메모 박스 시맨틱 렌더링 테스트 (W1)"""
from app.pipeline.epub_build import _build_chapter_html
from app.pipeline.layout import Block, BlockType, PageLayout


def _block(block_type, text="", level=0):
    return Block(block_type=block_type, bbox=(0, 0, 0, 0), confidence=1.0,
                 text=text, level=level)


# --- 목록 ---


def test_연속_list_item_세개는_하나의_ul로_묶인다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "- 사과"),
        _block(BlockType.LIST_ITEM, "- 바나나"),
        _block(BlockType.LIST_ITEM, "- 딸기"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert html.count("<ul>") == 1
    assert html.count("</ul>") == 1
    assert html.count("<li>") == 3
    assert "<p>* " not in html
    assert "<li>사과</li>" in html
    assert "<li>바나나</li>" in html
    assert "<li>딸기</li>" in html


def test_번호_목록은_ol로_렌더링된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "1. 첫째"),
        _block(BlockType.LIST_ITEM, "2. 둘째"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "<ol>" in html
    assert "</ol>" in html
    assert "<ul>" not in html
    assert "<li>첫째</li>" in html
    assert "<li>둘째</li>" in html


def test_list_item_사이에_paragraph가_끼면_목록이_둘로_나뉜다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "- 하나"),
        _block(BlockType.PARAGRAPH, "중간 문단"),
        _block(BlockType.LIST_ITEM, "- 둘"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert html.count("<ul>") == 2
    assert "<p>중간 문단</p>" in html


def test_한_블록에_여러줄_목록은_줄마다_항목이_된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "* 하나\n* 둘\n* 셋"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert html.count("<ul>") == 1
    assert html.count("<li>") == 3
    assert "<li>하나</li>" in html
    assert "<li>둘</li>" in html
    assert "<li>셋</li>" in html


def test_목록_항목_텍스트에도_인라인_마크다운이_적용된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "- 항목 **굵게**"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "<li>항목 <strong>굵게</strong></li>" in html


# --- 코드 블록 ---


def test_code_블록은_pre_code로_렌더링되고_펜스가_제거된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.CODE, "```python\nx = 1\n```"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "<pre><code>" in html
    assert "</code></pre>" in html
    assert "```" not in html
    assert "x = 1" in html


def test_code_블록은_인라인_마크다운을_변환하지_않는다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.CODE, "**not bold** and *not italic*"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "**not bold**" in html
    assert "*not italic*" in html
    assert "<strong>" not in html
    assert "<em>" not in html


def test_code_블록은_html_이스케이프된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.CODE, "if a < b: print(x & y)"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "a &lt; b" in html
    assert "x &amp; y" in html
    assert "<b:" not in html


def test_code_블록은_줄바꿈을_보존한다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.CODE, "line1\nline2\nline3"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "line1\nline2\nline3" in html


# --- aside(메모) 블록 ---


def test_aside_블록은_memo_클래스로_렌더링된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.ASIDE, "참고: **중요한** 메모입니다"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert '<aside class="memo">' in html
    assert "</aside>" in html
    assert "<p>" in html
    assert "<strong>중요한</strong>" in html


def test_aside_블록의_여러줄은_각각_p로_렌더링된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.ASIDE, "첫째 줄\n둘째 줄"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "<p>첫째 줄</p>" in html
    assert "<p>둘째 줄</p>" in html
