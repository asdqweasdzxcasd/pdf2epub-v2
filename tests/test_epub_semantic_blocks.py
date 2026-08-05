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


def test_번호_목록의_첫_마커_숫자가_ol_start로_보존된다():
    """"7."로 시작하는 번호 목록은 <ol>이 1번부터 다시 시작하지 않고
    <ol start="7">로 원래 번호를 보존해야 한다."""
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "7. 일곱째"),
        _block(BlockType.LIST_ITEM, "8. 여덟째"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert '<ol start="7">' in html
    assert "<li>일곱째</li>" in html
    assert "<li>여덟째</li>" in html


def test_1로_시작하는_번호_목록은_start_속성이_없다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "1. 첫째"),
        _block(BlockType.LIST_ITEM, "2. 둘째"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "<ol>" in html
    assert "start=" not in html


def test_불릿_목록과_번호_목록이_연속되면_따로_분리된다():
    """"- a", "- b" 다음에 "1. c", "2. d"가 바로 이어지면 마커 종류가
    바뀐 지점에서 목록을 분리해야 한다 (합쳐서 하나의 <ul>이나 <ol>로
    만들면 목록 종류가 뒤섞인다)."""
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.LIST_ITEM, "- a"),
        _block(BlockType.LIST_ITEM, "- b"),
        _block(BlockType.LIST_ITEM, "1. c"),
        _block(BlockType.LIST_ITEM, "2. d"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert html.count("<ul>") == 1
    assert html.count("</ul>") == 1
    assert html.count("<ol>") == 1
    assert html.count("</ol>") == 1
    # <ul>이 <ol>보다 앞서 나와야 한다 (원래 순서 보존)
    assert html.index("<ul>") < html.index("<ol>")
    for item in ("<li>a</li>", "<li>b</li>", "<li>c</li>", "<li>d</li>"):
        assert item in html


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


def test_aside_단일_줄바꿈은_문단으로_안_쪼개진다():
    """예전엔 블록 안의 단일 줄바꿈마다 <p>를 나눴으나, 그건 원본 지면의
    줄바꿈일 뿐이므로 이제는 같은 문단으로 이어붙여야 한다."""
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.ASIDE, "첫째 줄\n둘째 줄"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert html.count("<p>") == 1
    assert "<p>첫째 줄 둘째 줄</p>" in html


def test_aside_빈_줄로_구분된_문단은_각각_p로_렌더링된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.ASIDE, "첫째 문단\n\n둘째 문단"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert html.count("<p>") == 2
    assert "<p>첫째 문단</p>" in html
    assert "<p>둘째 문단</p>" in html


# --- 짧은 제목 heading (강등 제거) ---
#
# 길이/문자셋만으로는 OCR 잡음 라벨과 정당한 짧은 제목("AI", "US", "Go",
# "R", "C" 등)을 구별할 수 없다 (1글자로 좁혀도 "R", "C" 같은 프로그래밍
# 언어 이름은 여전히 오탐된다). 정당한 제목을 잃는 비용이 잡음 heading을
# 그대로 두는 비용보다 크므로, 강등 자체를 제거하고 짧은 heading도 그냥
# <h*>로 렌더링한다.


def test_두글자_영문_제목은_강등되지_않는다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.HEADING, "AI", level=1),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>AI</h1>" in html
    assert "<p>AI</p>" not in html


def test_한글자_영문_제목도_강등되지_않는다():
    """예전 정규식은 라틴 1~2자 heading을 전부 <p>로 강등했다 -- "R", "C"
    같은 정당한 1글자 제목까지 잃는 문제가 있었다. 강등 자체를 제거했으므로
    1글자 heading도 그대로 <h1>로 남는다."""
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.HEADING, "R", level=1),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "<h1>R</h1>" in html
    assert "<p>R</p>" not in html


# --- 코드 펜스 비대칭 제거 방지 ---


def test_여는_코드펜스만_있으면_원문이_보존된다():
    """Mistral이 닫는 펜스 없이 여는 펜스만 반환하는 경우, 코드 첫 줄이
    진짜 ``` 여도 조용히 사라지면 안 된다 -- 여는/닫는 펜스가 짝을 이룰
    때만 제거해야 한다."""
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.CODE, "```python\nx = 1"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "```python" in html
    assert "x = 1" in html


def test_짝이_맞는_코드펜스만_제거된다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.CODE, "```python\nx = 1\n```"),
    ])
    html = _build_chapter_html([layout], "장", {})
    assert "```" not in html
    assert "x = 1" in html
