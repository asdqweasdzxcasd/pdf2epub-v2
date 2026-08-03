"""배경 틴트 박스(<aside class="tinted">) 복원 렌더링 테스트

Block.bg(ocr_layout에서 페이지 이미지로부터 추출한 배경색)를 가진 텍스트
계열 블록들이 연속되고 색이 비슷하면 하나의 <aside class="tinted"> 박스로
묶여야 한다. 페이지 전체가 같은 색이면 그건 "박스"가 아니라 페이지
배경이므로 틴트를 적용하지 않는다.
"""
import colorsys

from app.pipeline.epub_build import _accent_color, _build_chapter_html
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
    """원본 지면과 대조한 결과, 강조 밴드 왼쪽에는 같은 색 계열의 선명한
    막대가 있다. _accent_color(HLS 채도 부스트 + 밝기 감소)로 만든 색이
    border-left-color로 나가야 한다 (단순히 채널마다 같은 비율로 어둡게
    누르면 파스텔 배경에서 회색이 되어버리므로 그 방식은 쓰지 않는다)."""
    # 페이지 전체가 같은 색이면 "페이지 배경"으로 보고 틴트를 걸지 않는
    # 가드가 있으므로, 흰 배경 본문 블록을 함께 둔다 (실제 지면과 동일한 구성)
    bg = (244, 248, 239)
    blocks = [
        Block(block_type=BlockType.HEADING, bbox=(0, 0, 0, 0), confidence=1.0,
              text="재시도 횟수와 간격", bg=bg),
        Block(block_type=BlockType.PARAGRAPH, bbox=(0, 0, 0, 0), confidence=1.0,
              text="재시도할 때는 다음 2가지를 결정해야 한다.", bg=None),
    ]
    html = _build_chapter_html([PageLayout(page_num=0, blocks=blocks)], "장", {})
    assert "background-color:#f4f8ef" in html
    expected_accent = "#%02x%02x%02x" % _accent_color(bg)
    assert f"border-left-color:{expected_accent}" in html


def test_accent_color는_파스텔_입력을_어둡고_채도_높은_비회색으로_바꾼다():
    """연분홍 파스텔처럼 채도가 낮은 배경도, 강조 막대는 (ㄱ) 원본보다
    어둡고 (ㄴ) 원본보다 채도가 높고 (ㄷ) 회색이 아니어야 한다(채널 최대
    차 30 이상) -- 채널별 동일 비율 감쇠(예: *0.55)는 이 세 조건 중 (ㄴ),
    (ㄷ)을 만족 못 해 회색(#8b8283 등)이 되므로 이 테스트가 그 회귀를 막는다."""
    pastel = (250, 235, 238)  # 연분홍
    accent = _accent_color(pastel)

    _, orig_l, orig_s = colorsys.rgb_to_hls(*(c / 255 for c in pastel))
    _, acc_l, acc_s = colorsys.rgb_to_hls(*(c / 255 for c in accent))

    assert acc_l < orig_l, "강조 막대는 원본보다 어두워야 한다"
    assert acc_s > orig_s, "강조 막대는 원본보다 채도가 높아야 한다"
    assert max(accent) - min(accent) >= 30, "강조 막대가 회색이면 안 된다"


def test_accent_color는_무채색_입력을_회색으로_유지한다():
    """회색(무채색) 배경은 존재하지 않는 색상을 억지로 만들면 안 되므로
    채도를 올리지 않고 밝기만 낮춰 계속 회색이어야 한다."""
    gray = (210, 210, 210)
    accent = _accent_color(gray)

    assert accent[0] == accent[1] == accent[2], "무채색 입력은 강조색도 무채색이어야 한다"
    assert sum(accent) < sum(gray), "그래도 원본보다는 어두워야 한다"
