"""_build_chapter_html의 PARAGRAPH 렌더링: 단일 줄바꿈은 문단 구분이 아니다.

실제 원인 진단: Mistral OCR은 한 문단 전체를 블록 하나로 주되, 원본 지면의
줄바꿈을 그대로 `\n`으로 보존한다. 예전 렌더링은 `text.split("\n")`로 줄마다
<p>를 만들어 한 문단이 여러 개로 쪼개졌고("는 소켓 타임아웃을..."처럼 조사로
시작하는 <p>가 생김). Mistral 응답은 마크다운이므로 마크다운 문단 규칙을
따라야 한다 -- 빈 줄만 문단 구분이고 단일 줄바꿈은 같은 문단으로 이어붙인다.
CODE만 예외로 줄바꿈을 그대로 보존한다.
"""
from app.pipeline.epub_build import _build_chapter_html
from app.pipeline.layout import Block, BlockType, PageLayout


def _block(block_type, text="", level=0):
    return Block(block_type=block_type, bbox=(0, 0, 0, 0), confidence=1.0,
                 text=text, level=level)


# --- PARAGRAPH: 단일 줄바꿈 vs 빈 줄 ---


def test_실측_사례_단일_줄바꿈_세개는_p_하나가_된다():
    """버그 리포트 실측 캐시 그대로: 지면 줄바꿈 3개가 있는 한 블록이
    <p> 4개로 쪼개지던 버그를 재현하고 고정한다."""
    text = (
        "읽기 타임아웃을 지정할 때는 실제로 설정하는 값이 무엇인지 확인해야 한다. "
        "예를 들어 Apache HttpClient\n"
        "는 소켓 타임아웃을 설정한다. 소켓 타임아웃은 네트워크 패킷 단위를 기준으로\n"
        "하므로, 전체 응답 시간에 대한\n"
        "타임아웃을 의미하지는 않는다. 실제로는 요청 하나가 5초 이상 걸릴 수\n"
        "있다."
    )
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.PARAGRAPH, text)])
    html = _build_chapter_html([layout], "장", {})

    assert html.count("<p>") == 1
    assert html.count("</p>") == 1
    assert "HttpClient는 소켓" in html  # 조사 병합 -- 공백 없음
    assert "걸릴 수 있다" in html  # 일반 이어붙임 -- 공백 있음


def test_빈_줄로_구분된_블록은_p_두개가_된다():
    text = "첫 번째 문단이다.\n\n두 번째 문단이다."
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.PARAGRAPH, text)])
    html = _build_chapter_html([layout], "장", {})

    assert html.count("<p>") == 2
    assert "<p>첫 번째 문단이다.</p>" in html
    assert "<p>두 번째 문단이다.</p>" in html


def test_줄바꿈_없는_단일_문단은_그대로_한_p():
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.PARAGRAPH, "한 줄짜리 문단")])
    html = _build_chapter_html([layout], "장", {})

    assert html.count("<p>") == 1
    assert "<p>한 줄짜리 문단</p>" in html


# --- CODE: 줄바꿈 보존 (회귀 방지) ---


def test_code_블록은_문단_병합_대상이_아니라_줄바꿈이_그대로_보존된다():
    text = "def f():\n    return 1\n\n\ndef g():\n    return 2"
    layout = PageLayout(page_num=0, blocks=[_block(BlockType.CODE, text)])
    html = _build_chapter_html([layout], "장", {})

    assert "<pre><code>" in html
    assert text in html  # 빈 줄, 들여쓰기, 개행 전부 원문 그대로


# --- CAPTION / FOOTNOTE: 줄바꿈이 있어도 문단으로 쪼개지지 않는다 ---


def test_독립_caption의_줄바꿈은_문단으로_안_쪼개진다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.CAPTION, "그림 5.1 이벤트 루프\n동작 방식"),
    ])
    html = _build_chapter_html([layout], "장", {})

    assert html.count("<p") == 1
    assert '<p class="caption">그림 5.1 이벤트 루프 동작 방식</p>' in html


def test_footnote의_줄바꿈은_문단으로_안_쪼개진다():
    layout = PageLayout(page_num=0, blocks=[
        _block(BlockType.FOOTNOTE, "각주 내용 첫 줄\n각주 내용 둘째 줄"),
    ])
    html = _build_chapter_html([layout], "장", {})

    assert html.count("<div class=\"footnote\">") == 1
    assert html.count("<p>") == 1
    assert "<p>각주 내용 첫 줄 각주 내용 둘째 줄</p>" in html


def test_figure_캡션의_줄바꿈도_병합된다():
    figure_block = Block(
        block_type=BlockType.FIGURE,
        bbox=(0, 0, 0, 0), confidence=1.0,
        image_path="fig1.png",
    )
    caption_block = _block(BlockType.CAPTION, "그림 1.\n설명이 길어서 두 줄이다")
    layout = PageLayout(page_num=0, blocks=[figure_block, caption_block])
    html = _build_chapter_html([layout], "장", {})

    assert "<figcaption>그림 1. 설명이 길어서 두 줄이다</figcaption>" in html
