"""캡션 + 표/코드 짝짓기 테스트

책 조판 관례상 표·리스팅 캡션은 대상 블록 "위"(앞)에 온다. 기존
_build_chapter_html은 FIGURE(이미지)만 캡션과 짝짓고 표/코드 캡션은
짝 없는 <p class="caption">로 남았다. 이 테스트는 CAPTION 다음 블록이
TABLE 또는 CODE인 경우를 짝짓는 확장 동작을 검증한다.
"""
from app.pipeline.epub_build import _build_chapter_html
from app.pipeline.layout import Block, BlockType, PageLayout


def _caption_block(text="캡션"):
    return Block(
        block_type=BlockType.CAPTION,
        bbox=(0, 0, 0, 0), confidence=1.0,
        text=text,
    )


def _table_block(text="", image_path=None):
    return Block(
        block_type=BlockType.TABLE,
        bbox=(0, 0, 0, 0), confidence=1.0,
        text=text, image_path=image_path,
    )


def _code_block(text="print(1)"):
    return Block(
        block_type=BlockType.CODE,
        bbox=(0, 0, 0, 0), confidence=1.0,
        text=text,
    )


# --- CAPTION + TABLE(텍스트) ---


def test_caption_다음_table_텍스트_병합():
    md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    layout = PageLayout(
        page_num=0,
        blocks=[_caption_block("표 5.1 이벤트 메시지와 커맨드 메시지"), _table_block(text=md)],
    )
    html = _build_chapter_html([layout], "장", {})

    assert "<table>" in html
    assert "<caption>표 5.1 이벤트 메시지와 커맨드 메시지</caption>" in html
    # caption은 table의 첫 자식 -- <table> 여는 태그 바로 뒤에 온다
    table_idx = html.index("<table>")
    caption_idx = html.index("<caption>")
    tr_idx = html.index("<tr>")
    assert table_idx < caption_idx < tr_idx
    # 소비된 캡션은 별도 <p class="caption">로 중복 출력하지 않는다
    assert 'class="caption"' not in html


# --- CAPTION + TABLE(이미지) ---


def test_caption_다음_table_이미지_병합():
    layout = PageLayout(
        page_num=0,
        blocks=[_caption_block("표 2.1 결과"), _table_block(image_path="table1.png")],
    )
    html = _build_chapter_html([layout], "장", {})

    assert "<table" in html
    assert "<caption>표 2.1 결과</caption>" in html
    assert 'src="images/table1.png"' in html
    assert 'class="caption"' not in html
    table_idx = html.index("<table")
    caption_idx = html.index("<caption>")
    img_idx = html.index("<img")
    assert table_idx < caption_idx < img_idx


# --- CAPTION + CODE ---


def test_caption_다음_code_병합():
    layout = PageLayout(
        page_num=0,
        blocks=[
            _caption_block("리스트 6.1 ReentrantLock을 사용해 락을 건다"),
            _code_block("lock.lock();\ntry { ... } finally { lock.unlock(); }"),
        ],
    )
    html = _build_chapter_html([layout], "장", {})

    assert '<figure class="listing">' in html
    assert (
        "<figcaption>리스트 6.1 ReentrantLock을 사용해 락을 건다</figcaption>"
        in html
    )
    assert "<pre><code>" in html
    assert 'class="caption"' not in html
    figcaption_idx = html.index("<figcaption")
    pre_idx = html.index("<pre>")
    assert figcaption_idx < pre_idx  # 캡션이 코드 위에 온다


# --- 경계: 짝이 안 맞으면 기존처럼 <p class="caption"> 유지 ---


def test_caption_다음이_아무것도_아니면_기존_p_유지():
    layout = PageLayout(page_num=0, blocks=[_caption_block("독립된 캡션")])
    html = _build_chapter_html([layout], "장", {})

    assert '<p class="caption">독립된 캡션</p>' in html
    assert "<figure" not in html
    assert "<table" not in html


def test_caption_다음_caption인_경우_죽지_않는다():
    layout = PageLayout(
        page_num=0,
        blocks=[_caption_block("캡션1"), _caption_block("캡션2")],
    )
    html = _build_chapter_html([layout], "장", {})

    assert '<p class="caption">캡션1</p>' in html
    assert '<p class="caption">캡션2</p>' in html


def test_caption_다음_빈_table은_짝짓지_않는다():
    # image_path도 text도 없는 TABLE 블록은 렌더링할 게 없으므로
    # caption을 소비하면 텍스트가 유실된다 -- 기존 <p class="caption"> 유지.
    layout = PageLayout(
        page_num=0,
        blocks=[_caption_block("빈 표 캡션"), _table_block()],
    )
    html = _build_chapter_html([layout], "장", {})

    assert '<p class="caption">빈 표 캡션</p>' in html
    assert "<caption>" not in html


# --- 회귀: 기존 FIGURE 짝짓기 ---


def test_기존_figure_caption_병합_회귀없음():
    figure_block = Block(
        block_type=BlockType.FIGURE,
        bbox=(0, 0, 0, 0), confidence=1.0,
        image_path="fig1.png",
    )
    layout = PageLayout(
        page_num=0, blocks=[figure_block, _caption_block("그림 1. 설명")]
    )
    html = _build_chapter_html([layout], "장", {})

    assert '<figure class="figure">' in html
    assert "<figcaption>그림 1. 설명</figcaption>" in html
    assert 'class="caption"' not in html
