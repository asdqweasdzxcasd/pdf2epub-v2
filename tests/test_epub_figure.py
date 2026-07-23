"""figure/caption 병합 → <figure><img/><figcaption> 렌더링 테스트"""
from app.pipeline.epub_build import _build_chapter_html
from app.pipeline.layout import Block, BlockType, PageLayout


def _figure_block(image_path="fig1.png"):
    return Block(
        block_type=BlockType.FIGURE,
        bbox=(0, 0, 0, 0), confidence=1.0,
        image_path=image_path,
    )


def _caption_block(text="그림 1. 설명"):
    return Block(
        block_type=BlockType.CAPTION,
        bbox=(0, 0, 0, 0), confidence=1.0,
        text=text,
    )


def test_figure_다음_caption_병합():
    layout = PageLayout(page_num=0, blocks=[_figure_block(), _caption_block("그림 1. 설명")])
    html = _build_chapter_html([layout], "장", {})

    assert '<figure class="figure">' in html
    assert '<img src="images/fig1.png" alt="그림"/>' in html
    assert "<figcaption>그림 1. 설명</figcaption>" in html
    assert html.count("<figure") == 1
    assert "</figure>" in html
    # 소비된 캡션은 별도 <p class="caption">로 중복 출력하지 않는다
    assert 'class="caption"' not in html


def test_caption_다음_figure_병합():
    layout = PageLayout(page_num=0, blocks=[_caption_block("그림 2. 설명"), _figure_block("fig2.png")])
    html = _build_chapter_html([layout], "장", {})

    assert '<img src="images/fig2.png" alt="그림"/>' in html
    assert "<figcaption>그림 2. 설명</figcaption>" in html
    assert html.count("<figure") == 1
    assert 'class="caption"' not in html
    # 병합된 마크업은 img가 먼저, figcaption이 뒤에 온다
    img_idx = html.index("<img")
    figcaption_idx = html.index("<figcaption")
    assert img_idx < figcaption_idx


def test_단독_figure는_figcaption_없이():
    layout = PageLayout(page_num=0, blocks=[_figure_block("fig3.png")])
    html = _build_chapter_html([layout], "장", {})

    assert '<figure class="figure"><img src="images/fig3.png" alt="그림"/></figure>' in html
    assert "<figcaption>" not in html


def test_단독_caption은_기존_p_유지():
    layout = PageLayout(page_num=0, blocks=[_caption_block("독립된 캡션")])
    html = _build_chapter_html([layout], "장", {})

    assert '<p class="caption">독립된 캡션</p>' in html
    assert "<figure" not in html
    assert "<figcaption>" not in html


def test_페이지_경계_넘어서는_병합_안함():
    page1 = PageLayout(page_num=0, blocks=[_figure_block("fig4.png")])
    page2 = PageLayout(page_num=1, blocks=[_caption_block("그림 4. 설명")])
    html = _build_chapter_html([page1, page2], "장", {})

    # 병합되지 않았으므로 figcaption 없이 단독 figure + 단독 caption p가 각각 출력
    assert '<figure class="figure"><img src="images/fig4.png" alt="그림"/></figure>' in html
    assert '<p class="caption">그림 4. 설명</p>' in html
    assert "<figcaption>" not in html


def test_table_formula는_기존_동작_유지():
    table_block = Block(
        block_type=BlockType.TABLE,
        bbox=(0, 0, 0, 0), confidence=1.0,
        image_path="table1.png",
    )
    formula_block = Block(
        block_type=BlockType.FORMULA,
        bbox=(0, 0, 0, 0), confidence=1.0,
        image_path="formula1.png",
    )
    layout = PageLayout(page_num=0, blocks=[table_block, formula_block])
    html = _build_chapter_html([layout], "장", {})

    assert '<div class="table-img"><img src="images/table1.png" alt="표"/></div>' in html
    assert '<div class="formula"><img src="images/formula1.png" alt="수식"/></div>' in html
