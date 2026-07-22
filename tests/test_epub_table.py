"""마크다운 표 → HTML 표 변환 테스트"""
from app.pipeline.epub_build import _build_chapter_html, markdown_table_to_html
from app.pipeline.layout import Block, BlockType, PageLayout


def test_기본_표_변환():
    md = "| 이름 | 값 |\n| --- | --- |\n| 응답시간 | 1초 |\n| 처리량 | 10 TPS |"
    html = markdown_table_to_html(md)
    assert "<table>" in html and "</table>" in html
    assert "<th>이름</th>" in html
    assert "<td>응답시간</td>" in html
    assert html.count("<tr>") == 3  # 헤더 1 + 데이터 2


def test_특수문자_이스케이프():
    md = "| a | b |\n| --- | --- |\n| <script> | 1&2 |"
    html = markdown_table_to_html(md)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "1&amp;2" in html


def test_표가_아닌_텍스트는_pre로_폴백():
    html = markdown_table_to_html("그냥 문장")
    assert html == "<pre>그냥 문장</pre>"


def test_chapter_html_테이블_텍스트_렌더링():
    block = Block(
        block_type=BlockType.TABLE,
        bbox=(0, 0, 0, 0), confidence=1.0,
        text="| a |\n| --- |\n| 1 |",
    )
    layout = PageLayout(page_num=0, blocks=[block])
    html = _build_chapter_html([layout], "장", {})
    assert "<table>" in html
