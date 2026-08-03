"""기본 스타일시트(_create_default_css) 규칙 검증 (W3 타이포그래피)

CSS 자체는 렌더링 결과를 단위 테스트하기 어려우므로, 핵심 설계 원칙이
실제로 CSS 문자열에 반영되어 있는지를 텍스트 검사로 확인한다.
"""
import re

from app.pipeline.epub_build import _create_default_css


def _css_text() -> str:
    item = _create_default_css()
    return item.content.decode("utf-8")


def test_한국어_조판_규칙이_포함된다():
    css = _css_text()
    assert "word-break: keep-all" in css
    assert "overflow-wrap: break-word" in css


def test_pre는_가로스크롤_대신_줄바꿈한다():
    css = _css_text()
    assert "white-space: pre-wrap" in css


def test_aside_memo_스타일이_있다():
    css = _css_text()
    assert "aside.memo" in css


def test_다크모드_보정_블록이_있다():
    css = _css_text()
    assert "@media (prefers-color-scheme: dark)" in css


def test_본문_body에는_font_family를_지정하지_않는다():
    """리더 기본 폰트를 존중해야 하므로 body 셀렉터 규칙 블록에
    font-family가 있으면 안 된다 (pre/code 등 모노스페이스 전용은 허용)."""
    css = _css_text()
    match = re.search(r"(?<![\w.#])body\s*\{([^}]*)\}", css)
    assert match is not None, "body 규칙을 찾지 못함"
    assert "font-family" not in match.group(1)


def test_px_단위를_쓰지_않는다():
    css = _css_text()
    # border 두께 표기(1px solid 등)를 포함해 어디에도 px를 쓰지 않는다
    assert "px" not in css


def test_line_height는_17에서_18_사이다():
    css = _css_text()
    match = re.search(r"(?<![\w.#])body\s*\{([^}]*)\}", css)
    assert match is not None
    lh_match = re.search(r"line-height:\s*([\d.]+)", match.group(1))
    assert lh_match is not None
    lh = float(lh_match.group(1))
    assert 1.7 <= lh <= 1.8


def test_제목_계층_h1_h2_h3가_구분된다():
    css = _css_text()
    assert "h1 {" in css or "h1{" in css
    assert "h2 {" in css or "h2{" in css
    assert "h3 {" in css or "h3{" in css
    assert "page-break-after: avoid" in css


def test_tinted_스타일이_있고_글자색이_어둡게_고정된다():
    """.tinted는 인라인 배경색(파스텔)이 다크 테마에서도 안 뒤집히므로,
    글자색을 어둡게 고정해 어떤 테마에서도 읽히게 해야 한다."""
    css = _css_text()
    assert ".tinted" in css
    assert "page-break-inside: avoid" in css
    match = re.search(r"\.tinted,\s*\.tinted \*\s*\{([^}]*)\}", css)
    assert match is not None, ".tinted 글자색 고정 규칙을 찾지 못함"
    assert "color: #1a1a1a" in match.group(1)


def test_figure와_table은_페이지_분리를_피한다():
    css = _css_text()
    figure_match = re.search(r"figure\.figure\s*\{([^}]*)\}", css)
    assert figure_match is not None
    assert "page-break-inside: avoid" in figure_match.group(1)

    table_match = re.search(r"(?<![\w.#-])table\s*\{([^}]*)\}", css)
    assert table_match is not None
    assert "page-break-inside: avoid" in table_match.group(1)
