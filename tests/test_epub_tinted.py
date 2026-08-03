"""배경 틴트 박스(<aside class="tinted">) 복원 렌더링 테스트

Block.bg(ocr_layout에서 페이지 이미지로부터 추출한 배경색)를 가진 텍스트
계열 블록들이 연속되고 색이 비슷하면 하나의 <aside class="tinted"> 박스로
묶여야 한다. 페이지 전체가 같은 색이면 그건 "박스"가 아니라 페이지
배경이므로 틴트를 적용하지 않는다.
"""
from app.pipeline.epub_build import _build_chapter_html
from app.pipeline.layout import Block, BlockType, PageLayout


def _heading(text="제목", bg=None):
    return Block(
        block_type=BlockType.HEADING, bbox=(0, 0, 0, 0), confidence=1.0,
        text=text, level=1, bg=bg,
    )


def _para(text="문단", bg=None):
    return Block(
        block_type=BlockType.PARAGRAPH, bbox=(0, 0, 0, 0), confidence=1.0,
        text=text, bg=bg,
    )


PASTEL_A = (244, 248, 239)  # -> #f4f8ef
PASTEL_B = (240, 245, 251)  # -> #f0f5fb (A와 채널 최대 차 12 > 8)


def test_같은_배경색_연속_블록_3개는_하나의_aside로_묶인다():
    # 앞에 흰 배경(bg=None) 문단을 하나 둬서 "페이지 전체가 같은 색" 가드에
    # 걸리지 않게 한다 -- 이 테스트가 검증하려는 건 어디까지나 부분 박스다.
    blocks = [
        _para("들어가는 말", bg=None),
        _heading("제목", bg=PASTEL_A),
        _para("문단1", bg=PASTEL_A),
        _para("문단2", bg=PASTEL_A),
    ]
    layout = PageLayout(page_num=0, blocks=blocks)
    html = _build_chapter_html([layout], "장", {})

    assert html.count('<aside class="tinted"') == 1
    assert "background-color:#f4f8ef" in html
    aside_start = html.index('<aside class="tinted"')
    aside_end = html.index("</aside>")
    inner = html[aside_start:aside_end]
    assert "제목" in inner
    assert "문단1" in inner
    assert "문단2" in inner


def test_배경색이_다르면_박스가_분리된다():
    blocks = [
        _para("문단A", bg=PASTEL_A),
        _para("문단B", bg=PASTEL_B),
    ]
    layout = PageLayout(page_num=0, blocks=blocks)
    html = _build_chapter_html([layout], "장", {})

    assert html.count('<aside class="tinted"') == 2
    assert "background-color:#f4f8ef" in html
    assert "background-color:#f0f5fb" in html


def test_페이지_전체가_같은_색이면_틴트_미적용():
    blocks = [
        _heading("제목", bg=PASTEL_A),
        _para("문단", bg=PASTEL_A),
    ]
    layout = PageLayout(page_num=0, blocks=blocks)
    html = _build_chapter_html([layout], "장", {})

    assert '<aside class="tinted"' not in html
    assert "<h1>제목</h1>" in html
    assert "<p>문단</p>" in html


def test_배경색_없는_블록은_틴트박스_밖에서_렌더된다():
    blocks = [
        _para("흰 문단", bg=None),
        _para("색 문단", bg=PASTEL_A),
    ]
    layout = PageLayout(page_num=0, blocks=blocks)
    html = _build_chapter_html([layout], "장", {})

    assert html.count('<aside class="tinted"') == 1
    aside_start = html.index('<aside class="tinted"')
    assert "흰 문단" in html[:aside_start]
    assert "색 문단" in html[aside_start:]


def test_기존_bg_없는_블록들은_회귀_없이_렌더된다():
    """bg 필드를 아예 안 쓰는 기존 호출부(전부 None)는 틴트 관련 변경으로
    출력이 달라지면 안 된다."""
    blocks = [_heading("제목"), _para("문단1"), _para("문단2")]
    layout = PageLayout(page_num=0, blocks=blocks)
    html = _build_chapter_html([layout], "장", {})

    assert '<aside class="tinted"' not in html
    assert "<h1>제목</h1>" in html
    assert "<p>문단1</p>" in html
    assert "<p>문단2</p>" in html


def test_틴트_박스에_배경보다_진한_왼쪽_막대색이_붙는다():
    """원본 지면과 대조한 결과, 강조 밴드 왼쪽에는 같은 계열의 진한 막대가
    있다. 배경색을 어둡게 눌러 만든 색이 border-left-color로 나가야 한다."""
    # 페이지 전체가 같은 색이면 "페이지 배경"으로 보고 틴트를 걸지 않는
    # 가드가 있으므로, 흰 배경 본문 블록을 함께 둔다 (실제 지면과 동일한 구성)
    blocks = [
        Block(block_type=BlockType.HEADING, bbox=(0, 0, 0, 0), confidence=1.0,
              text="재시도 횟수와 간격", bg=(244, 248, 239)),
        Block(block_type=BlockType.PARAGRAPH, bbox=(0, 0, 0, 0), confidence=1.0,
              text="재시도할 때는 다음 2가지를 결정해야 한다.", bg=None),
    ]
    html = _build_chapter_html([PageLayout(page_num=0, blocks=blocks)], "장", {})
    assert "background-color:#f4f8ef" in html
    assert "border-left-color:#868883" in html  # 각 채널 * 0.55
