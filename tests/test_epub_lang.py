"""챕터 XHTML의 <html> 태그에 한국어 lang 속성이 들어가는지 검증 (W3)"""
from app.pipeline.epub_build import _build_chapter_html
from app.pipeline.layout import Block, BlockType, PageLayout


def test_챕터_html_태그에_lang_ko가_있다():
    layout = PageLayout(page_num=0, blocks=[
        Block(block_type=BlockType.PARAGRAPH, bbox=(0, 0, 0, 0),
              confidence=1.0, text="본문"),
    ])
    html = _build_chapter_html([layout], "장", {})

    html_tag_match = html.split("<body>")[0]
    assert 'lang="ko"' in html_tag_match
    assert 'xml:lang="ko"' in html_tag_match
