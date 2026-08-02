"""ebooklib 기반 EPUB3 조립"""

import logging
import re
from pathlib import Path
from uuid import uuid4

from ebooklib import epub

from app.pipeline.markdown_inline import to_xhtml

logger = logging.getLogger(__name__)

# 목록 항목 줄머리 마커: "- ", "* ", "+ ", "• ", "1. ", "1) " 등
# 번호 마커는 숫자를 그룹 1로 캡처해 <ol start="N">에 쓴다.
_LIST_MARKER_RE = re.compile(r"^(?:(\d+)[.)]|[-*+•])\s+")

# 코드 펜스 줄 (``` 또는 ```lang)
_CODE_FENCE_LINE_RE = re.compile(r"^```\S*$")

# 짧은 heading(라틴 1~2자 등)을 문단으로 강등하는 규칙은 두지 않는다.
#
# 이전에는 "^[A-Za-z]{1,2}$" 로 라틴 1~2자 heading을 전부 <p>로 강등했으나,
# "AI", "US", "Go" 같은 2글자 제목과 "R", "C" 같은 1글자 제목(프로그래밍
# 언어명 등)까지 함께 강등되는 오탐이 있었다. 1글자만 강등하도록 좁혀도
# "R", "C" 오탐은 그대로 남는다 -- 길이/문자셋만으로는 OCR 잡음 라벨과
# 정당한 짧은 제목을 구별할 방법이 없다. 정당한 제목을 잃는 비용이 잡음
# heading을 그대로 두는 비용보다 크므로, 강등 자체를 제거하고 짧은
# heading도 다른 heading과 동일하게 <h*>로 렌더링한다.

# EPUB 기본 스타일시트.
#
# 설계 원칙 (모두 지킬 것):
# - body에 font-family를 지정하지 않는다 -- 리더에서 사용자가 고른 폰트/크기를
#   덮어쓰지 않기 위함. 모노스페이스가 필요한 pre/code에만 지정한다.
# - 길이 단위는 전부 em/rem/%만 쓴다 (px 금지) -- 사용자가 글자 크기를
#   키워도 여백/테두리가 함께 커지도록.
# - 배경/글자색은 하드코딩하지 않는다. currentColor + rgba 투명도 오버레이로
#   최소한만 쓰고, 옅은 오버레이는 어두운 배경에서 안 보이므로 아래
#   @media (prefers-color-scheme: dark) 블록에서 밝은 계열로 뒤집는다.
# - 한국어 조판: line-height 1.7~1.8, word-break: keep-all(단어 중간 줄바꿈
#   방지), overflow-wrap: break-word(긴 URL 등 대비).
DEFAULT_CSS = """\
/* ============================================================
   전역 -- 본문 폰트는 리더 기본값을 그대로 쓴다 (font-family 지정 없음)
   ============================================================ */
body {
  line-height: 1.75;
  margin: 1em;
  word-break: keep-all;
  overflow-wrap: break-word;
}

/* ============================================================
   제목 계층 -- h1(장) > h2(절) > h3(항)이 크기/여백으로 뚜렷이 구분되게.
   제목이 페이지 중간에서 잘리지 않도록 page-break-after: avoid.
   ============================================================ */
h1, h2, h3 {
  line-height: 1.3;
  page-break-after: avoid;
  word-break: keep-all;
}

h1 {
  font-size: 1.7em;
  margin: 2.2em 0 1em;
}

h2 {
  font-size: 1.3em;
  margin: 1.6em 0 0.8em;
}

h3 {
  font-size: 1.1em;
  margin: 1.2em 0 0.6em;
}

/* ============================================================
   본문 문단 -- 한국어 책 관례상 "첫 줄 들여쓰기" 방식을 택한다.
   들여쓰기와 문단 간 margin을 함께 쓰면 구분 표시가 중복돼 지저분해지므로
   margin은 0으로 둔다 (여백 방식과 들여쓰기 방식 중 하나만 사용).
   제목 바로 다음 첫 문단은 들여쓰지 않는다.
   ============================================================ */
p {
  text-indent: 1em;
  margin: 0;
}

h1 + p, h2 + p, h3 + p {
  text-indent: 0;
}

/* ============================================================
   figure/figcaption -- 그림은 가운데 정렬, 캡션은 작고 차분하게.
   그림과 캡션이 페이지 경계에서 분리되지 않도록 page-break-inside: avoid.
   ============================================================ */
figure.figure {
  text-align: center;
  margin: 1.5em 0;
  page-break-inside: avoid;
}
figure.figure img {
  max-width: 100%;
  height: auto;
}
figcaption {
  font-size: 0.85em;
  margin-top: 0.5em;
  opacity: 0.75;
}

.table-img, .formula {
  text-align: center;
  margin: 1em 0;
}
.table-img img, .formula img {
  max-width: 100%;
  height: auto;
}
.formula img {
  max-width: 80%;
}
.caption {
  font-size: 0.85em;
  text-align: center;
  opacity: 0.75;
}

/* ============================================================
   pre/code -- 리더에는 가로 스크롤이 없으므로 pre-wrap으로 줄바꿈한다.
   코드 영역임을 옅은 배경 + 왼쪽 테두리로 표시. 모노스페이스는
   여기에만 지정한다 (본문 폰트는 건드리지 않음).
   ============================================================ */
pre {
  background: rgba(0, 0, 0, 0.05);
  border-left: 0.25em solid currentColor;
  padding: 0.8em 1em;
  margin: 1em 0;
  font-size: 0.85em;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: break-word;
}
pre, code {
  font-family: monospace;
}
code {
  background: rgba(0, 0, 0, 0.05);
  padding: 0.1em 0.3em;
  border-radius: 0.2em;
  font-size: 0.85em;
}
pre code {
  background: none;
  padding: 0;
  font-size: 1em;
}

/* ============================================================
   table -- 헤더 구분, 셀 패딩, 표가 페이지 중간에서 잘리지 않게.
   ============================================================ */
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1.2em 0;
  font-size: 0.9em;
  page-break-inside: avoid;
}
th, td {
  border: 0.06em solid currentColor;
  padding: 0.4em 0.6em;
  text-align: left;
}
th {
  background: rgba(0, 0, 0, 0.08);
}

/* ============================================================
   aside.memo -- 본문과 구별되는 박스 (옅은 배경 + 왼쪽 굵은 테두리)
   ============================================================ */
aside.memo {
  background: rgba(0, 0, 0, 0.05);
  border-left: 0.3em solid currentColor;
  padding: 0.7em 1.1em;
  margin: 1.2em 0;
}
aside.memo p {
  text-indent: 0;
}

/* ============================================================
   목록 -- 항목 간 여백과 들여쓰기
   ============================================================ */
ul, ol {
  margin: 0.8em 0;
  padding-left: 1.6em;
}
li {
  margin: 0.4em 0;
  line-height: 1.7;
}

/* ============================================================
   위/아래 첨자 -- line-height: 0 트릭으로 줄 높이를 밀지 않게 한다.
   ============================================================ */
sup, sub {
  font-size: 0.7em;
  line-height: 0;
  position: relative;
  vertical-align: baseline;
}
sup {
  top: -0.5em;
}
sub {
  bottom: -0.25em;
}

/* ============================================================
   각주 -- 본문과 구분되게 작고, 위에 구분선.
   ============================================================ */
.footnote {
  font-size: 0.85em;
  border-top: 0.06em solid currentColor;
  opacity: 0.85;
  padding-top: 0.6em;
  margin-top: 2em;
}

/* ============================================================
   다크 모드 보정 -- 옅은 검정 오버레이는 어두운 배경에서 거의 안 보이므로
   밝은 계열 오버레이로 뒤집는다. 글자색/배경 자체는 리더가 관리하므로
   여기서는 건드리지 않는다.
   ============================================================ */
@media (prefers-color-scheme: dark) {
  pre, code {
    background: rgba(255, 255, 255, 0.08);
  }
  aside.memo {
    background: rgba(255, 255, 255, 0.08);
  }
  th {
    background: rgba(255, 255, 255, 0.1);
  }
}
"""


def build_epub(
    page_layouts: list,
    toc_entries: list,
    figures_dir: Path,
    output_path: Path,
    title: str = "Converted Book",
    progress_cb=None,
) -> Path:
    """레이아웃 결과와 목차로 EPUB3 파일을 생성한다.

    Args:
        page_layouts: list[PageLayout] - layout.py의 결과
        toc_entries: list[TocEntry] - toc.py의 결과
        figures_dir: 크롭된 이미지 디렉토리 (temp/{job_id}/figures/)
        output_path: 최종 .epub 출력 경로
        title: 책 제목 (기본: "Converted Book")
        progress_cb: 진행률 콜백 (ProgressCallback 프로토콜)

    Returns:
        생성된 EPUB 파일 경로
    """
    if progress_cb:
        progress_cb.update(90, "epub_build", "EPUB 생성 시작")

    book = epub.EpubBook()

    # 메타데이터 설정
    book_id = uuid4().hex[:8]
    book.set_identifier(f"ebook-converter-{book_id}")
    book.set_title(title)
    book.set_language("ko")
    book.add_author("ebook-converter")

    # 기본 CSS
    css = _create_default_css()
    book.add_item(css)

    # figures 디렉토리의 이미지를 EPUB에 등록
    image_items = _register_images(book, figures_dir)

    # 목차 기준으로 페이지를 챕터 단위로 분할
    # has_toc: 진짜 목차가 있는지 (False면 단일 본문, ToC/nav에 항목 추가 안 함)
    has_toc = bool(toc_entries)
    chapters = _split_into_chapters(page_layouts, toc_entries, fallback_title=title)

    # spine은 챕터들로만 시작. nav.xhtml은 spine 첫 항목으로 안 넣음 →
    # 리더의 ToC 메뉴로만 작동, 본문 첫 페이지는 chapter_000부터.
    spine_items: list = []
    toc_links: list = []
    total_chapters = len(chapters)

    for idx, (chapter_title, chapter_layouts) in enumerate(chapters):
        chapter_id = f"chapter_{idx:03d}"
        filename = f"{chapter_id}.xhtml"

        # 챕터 HTML 생성 (본문 자체엔 챕터 제목 안 박음)
        html_content = _build_chapter_html(
            chapter_layouts, chapter_title, image_items
        )

        # 빈 챕터 방지 (ebooklib이 빈 body를 파싱하지 못함)
        if not any(
            tag in html_content
            for tag in ("<h1>", "<p>", "<div", "<img", "<pre", "<ul", "<ol", "<aside")
        ):
            html_content = html_content.replace(
                "</body>",
                f"<p>{_escape_html(chapter_title)}</p>\n</body>",
            )

        chapter = epub.EpubHtml(
            title=chapter_title,
            file_name=filename,
            lang="ko",
        )
        chapter.content = html_content.encode("utf-8")
        chapter.add_item(css)

        book.add_item(chapter)
        spine_items.append(chapter)

        # ToC에는 진짜 목차가 있을 때만 항목 추가 (단일 본문일 땐 ToC 비움)
        if has_toc:
            toc_links.append(epub.Link(filename, chapter_title, chapter_id))

        # 진행률 (epub_build: 90~100)
        if progress_cb:
            pct = 90 + int((idx + 1) / total_chapters * 10)
            progress_cb.update(
                pct, "epub_build", f"챕터 {idx + 1}/{total_chapters}"
            )

    # TOC 및 네비게이션 설정
    book.toc = toc_links
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine_items

    # 파일 저장
    output_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(output_path), book)

    if progress_cb:
        progress_cb.update(100, "epub_build", "EPUB 생성 완료")

    logger.info("EPUB 생성 완료: %s", output_path)
    return output_path


def _create_default_css() -> epub.EpubItem:
    """EPUB용 기본 CSS를 생성한다."""
    return epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=DEFAULT_CSS.encode("utf-8"),
    )


def _register_images(
    book: epub.EpubBook,
    figures_dir: Path,
) -> dict[str, epub.EpubItem]:
    """figures 디렉토리의 PNG 이미지를 EPUB에 등록한다.

    Returns:
        {파일명: EpubItem} 딕셔너리
    """
    image_items: dict[str, epub.EpubItem] = {}

    if not figures_dir.exists():
        return image_items

    for img_file in sorted(figures_dir.glob("*.png")):
        img_data = img_file.read_bytes()
        img_item = epub.EpubItem(
            uid=f"img_{img_file.stem}",
            file_name=f"images/{img_file.name}",
            media_type="image/png",
            content=img_data,
        )
        book.add_item(img_item)
        image_items[img_file.name] = img_item

    return image_items


def _split_into_chapters(
    page_layouts: list,
    toc_entries: list,
    fallback_title: str = "본문",
) -> list[tuple[str, list]]:
    """목차 기준으로 페이지 레이아웃을 챕터 단위로 분할한다.

    목차의 각 항목을 챕터 경계로 사용한다.
    목차 첫 항목 이전에 페이지가 있으면 "서문"으로 묶는다.

    Args:
        page_layouts: PageLayout 리스트
        toc_entries: 목차 (비어있으면 단일 챕터)
        fallback_title: 목차 없을 때 단일 챕터의 (메타데이터용) 제목.
                        본문 자체에는 표시되지 않음.

    Returns:
        [(chapter_title, [PageLayout, ...]), ...]
    """
    if not toc_entries:
        return [(fallback_title, page_layouts)]

    # page_num 기준 정렬
    sorted_toc = sorted(toc_entries, key=lambda e: e.page_num)

    chapters: list[tuple[str, list]] = []

    # 목차 첫 항목 이전 페이지를 "서문"으로 묶기
    if sorted_toc[0].page_num > 0:
        front_pages = [
            layout
            for layout in page_layouts
            if layout.page_num < sorted_toc[0].page_num
        ]
        if front_pages:
            chapters.append(("서문", front_pages))

    # 각 목차 항목에 해당하는 페이지 범위 수집
    for i, entry in enumerate(sorted_toc):
        start_page = entry.page_num
        if i + 1 < len(sorted_toc):
            end_page = sorted_toc[i + 1].page_num
        else:
            end_page = len(page_layouts)

        chapter_pages = [
            layout
            for layout in page_layouts
            if start_page <= layout.page_num < end_page
        ]

        if chapter_pages:
            chapters.append((entry.title, chapter_pages))

    # 빈 결과 방지
    if not chapters:
        return [("본문", page_layouts)]

    return chapters


def _build_chapter_html(
    chapter_layouts: list,
    chapter_title: str,
    image_items: dict[str, object],
) -> str:
    """챕터의 XHTML 컨텐츠를 생성한다.

    block_type별 변환 규칙:
    - heading → <h1>/<h2>/<h3> (level 기준. 짧은 제목이라고 강등하지 않음 -
      이유는 _NOISE_HEADING_RE 자리의 주석 참고)
    - paragraph → <p> (줄바꿈 단위로 분리)
    - list_item → 같은 페이지에서 연속된 LIST_ITEM들을 목록으로 묶는다.
      마커 종류(불릿 vs 번호)가 바뀌는 지점에서 목록을 분리한다 (합치지
      않음). 번호 목록은 첫 항목의 마커 숫자를 <ol start="N">으로 보존한다
      (N이 1이면 start 속성 생략). 항목 텍스트의 마크다운 목록 마커
      (-, *, +, •, 1., 1) 등)는 제거하고 to_xhtml 적용
    - code → <pre><code> (내부는 이스케이프만 하고 인라인 마크다운 변환은
      하지 않는다. 여는/닫는 코드 펜스(```)가 짝을 이룰 때만 제거하고
      줄바꿈은 보존)
    - aside → <aside class="memo"> 안에 본문처럼 <p> (to_xhtml 적용)
    - figure(이미지 있음) + 인접 caption → <figure><img/><figcaption> 병합
      (같은 PageLayout 안에서 FIGURE 바로 다음이 CAPTION이거나, CAPTION 바로
      다음이 FIGURE인 경우만 병합. 페이지 경계를 넘는 병합은 하지 않음)
    - 짝 없는 figure → <figure class="figure"><img/></figure>
    - 짝 없는 caption → <p class="caption">
    - table/formula → 이미지가 있으면 <img>, 없으면 alt 텍스트
    - footnote → <div class="footnote">
    - page_header, page_footer → EPUB에서 제외
    """
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!DOCTYPE html>",
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="ko" xml:lang="ko">',
        "<head>",
        f"<title>{_escape_html(chapter_title)}</title>",
        '<link rel="stylesheet" type="text/css" href="style/default.css"/>',
        "</head>",
        "<body>",
    ]

    for layout in chapter_layouts:
        blocks = layout.blocks
        n = len(blocks)
        i = 0
        while i < n:
            block = blocks[i]
            bt = _block_type_str(block)

            if bt == "heading":
                text = block.text.strip() if block.text else ""
                if text:
                    tag = _heading_tag(getattr(block, "level", 0))
                    parts.append(f"<{tag}>{to_xhtml(text)}</{tag}>")

            elif bt == "paragraph":
                text = block.text.strip() if block.text else ""
                if text:
                    # 줄바꿈 단위로 <p> 분리
                    for line in text.split("\n"):
                        line = line.strip()
                        if line:
                            parts.append(f"<p>{to_xhtml(line)}</p>")

            elif bt == "list_item":
                # 연속된 LIST_ITEM 블록들을 하나의 <ul>/<ol>로 묶는다.
                # 중간에 다른 타입이 오면 목록이 끝난다.
                j = i
                flat_items: list[tuple[str, bool, int | None]] = []
                while j < n and _block_type_str(blocks[j]) == "list_item":
                    raw_text = blocks[j].text or ""
                    for raw_line in raw_text.split("\n"):
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        item_text, is_numbered, marker_num = _strip_list_marker(
                            raw_line
                        )
                        if item_text:
                            flat_items.append((item_text, is_numbered, marker_num))
                    j += 1

                for ordered, start_num, run_items in _group_list_runs(flat_items):
                    tag = "ol" if ordered else "ul"
                    if tag == "ol" and start_num not in (None, 1):
                        parts.append(f'<{tag} start="{start_num}">')
                    else:
                        parts.append(f"<{tag}>")
                    for item in run_items:
                        parts.append(f"<li>{to_xhtml(item)}</li>")
                    parts.append(f"</{tag}>")

                i = j
                continue

            elif bt == "code":
                text = block.text if block.text else ""
                if text.strip():
                    parts.append(_code_html(text))

            elif bt == "aside":
                text = block.text.strip() if block.text else ""
                if text:
                    inner_parts = [
                        f"<p>{to_xhtml(line.strip())}</p>"
                        for line in text.split("\n")
                        if line.strip()
                    ]
                    if inner_parts:
                        parts.append('<aside class="memo">')
                        parts.extend(inner_parts)
                        parts.append("</aside>")

            elif bt == "figure" and block.image_path:
                next_block = blocks[i + 1] if i + 1 < n else None
                if next_block is not None and _block_type_str(next_block) == "caption":
                    caption_text = (
                        next_block.text.strip() if next_block.text else ""
                    )
                    parts.append(_figure_html(block.image_path, caption_text))
                    i += 2
                    continue
                parts.append(_figure_html(block.image_path))

            elif bt == "table":
                if block.image_path:
                    img_src = f"images/{block.image_path}"
                    parts.append(
                        f'<div class="table-img">'
                        f'<img src="{img_src}" alt="표"/>'
                        f"</div>"
                    )
                elif block.text and block.text.strip():
                    parts.append(markdown_table_to_html(block.text))

            elif bt == "formula" and block.image_path:
                img_src = f"images/{block.image_path}"
                parts.append(
                    f'<div class="formula">'
                    f'<img src="{img_src}" alt="수식"/>'
                    f"</div>"
                )

            elif bt == "caption":
                next_block = blocks[i + 1] if i + 1 < n else None
                if (
                    next_block is not None
                    and _block_type_str(next_block) == "figure"
                    and next_block.image_path
                ):
                    caption_text = block.text.strip() if block.text else ""
                    parts.append(_figure_html(next_block.image_path, caption_text))
                    i += 2
                    continue
                text = block.text.strip() if block.text else ""
                if text:
                    parts.append(
                        f'<p class="caption">{to_xhtml(text)}</p>'
                    )

            elif bt == "footnote":
                text = block.text.strip() if block.text else ""
                if text:
                    parts.append(
                        f'<div class="footnote">'
                        f"<p>{to_xhtml(text)}</p>"
                        f"</div>"
                    )

            # page_header, page_footer는 EPUB에서 제외 (무시)

            i += 1

    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


def _block_type_str(block) -> str:
    """Block.block_type을 문자열로 정규화한다 (Enum이든 str이든)."""
    return (
        block.block_type.value
        if hasattr(block.block_type, "value")
        else str(block.block_type)
    )


def _strip_list_marker(line: str) -> tuple[str, bool, int | None]:
    """목록 항목 줄머리 마커를 제거한다.

    Returns:
        (마커를 제거한 텍스트, 번호 마커였는지 여부, 번호 마커의 숫자
        (번호 마커가 아니면 None))
    """
    match = _LIST_MARKER_RE.match(line)
    if not match:
        return line, False, None
    digits = match.group(1)
    is_numbered = digits is not None
    marker_num = int(digits) if is_numbered else None
    return line[match.end():].strip(), is_numbered, marker_num


def _group_list_runs(
    flat_items: list[tuple[str, bool, int | None]],
) -> list[tuple[bool, int | None, list[str]]]:
    """마커 종류(불릿 vs 번호)가 바뀌는 지점마다 목록을 나눈다.

    "- a", "- b", "1. c", "2. d"처럼 불릿 목록 다음에 번호 목록이 바로
    이어지는 경우, 둘을 하나의 <ul>/<ol>로 합치면 목록 종류가 뒤섞인다.
    연속된 항목을 마커 종류가 같은 구간(run)으로 나눠 각 구간을 별도
    목록으로 렌더링한다.

    Args:
        flat_items: [(항목 텍스트, 번호 마커 여부, 마커 숫자), ...]
                    (블록/줄 경계는 이미 펼쳐진 상태)

    Returns:
        [(ordered, start_num, [항목 텍스트, ...]), ...]
        - ordered: 번호 목록이면 True
        - start_num: 번호 목록의 첫 항목 마커 숫자 (불릿 목록이면 None)
    """
    runs: list[tuple[bool, int | None, list[str]]] = []
    current_ordered: bool | None = None
    current_start: int | None = None
    current_items: list[str] = []

    for item_text, is_numbered, marker_num in flat_items:
        if current_ordered is None or is_numbered != current_ordered:
            if current_items:
                runs.append((current_ordered, current_start, current_items))
            current_ordered = is_numbered
            current_start = marker_num if is_numbered else None
            current_items = []
        current_items.append(item_text)

    if current_items:
        runs.append((current_ordered, current_start, current_items))

    return runs


def _code_html(text: str) -> str:
    """CODE 블록을 <pre><code>로 렌더링한다.

    인라인 마크다운 변환은 하지 않고 HTML 특수문자만 이스케이프한다
    (코드 안의 `**` 등이 문법일 수 있어 강조로 오변환되면 안 됨).
    Mistral이 ```lang ... ``` 펜스로 감싸는 경우 앞뒤 펜스 줄을 제거한다.
    줄바꿈은 그대로 보존한다.
    """
    content = _strip_code_fence(text.strip("\n"))
    return f"<pre><code>{_escape_html(content)}</code></pre>"


def _strip_code_fence(text: str) -> str:
    """코드 블록 앞뒤의 마크다운 펜스(``` 또는 ```lang) 줄을 제거한다.

    여는 펜스와 닫는 펜스가 짝을 이룰 때만 제거한다. 각각 독립적으로
    제거하면 코드 첫/마지막 줄이 진짜 ``` 인 코드 블록(펜스가 한쪽만
    있거나 아예 없는 경우)에서 그 줄이 조용히 사라진다.
    """
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    has_open = bool(_CODE_FENCE_LINE_RE.match(lines[0].strip()))
    has_close = lines[-1].strip() == "```"
    if has_open and has_close:
        return "\n".join(lines[1:-1])
    return text


def _figure_html(image_path: str, caption_text: str = "") -> str:
    """<figure> 마크업을 생성한다. caption_text가 있으면 <figcaption>을 함께 담는다."""
    img_src = f"images/{image_path}"
    img_tag = f'<img src="{img_src}" alt="그림"/>'
    if caption_text:
        return (
            f'<figure class="figure">{img_tag}'
            f"<figcaption>{to_xhtml(caption_text)}</figcaption>"
            f"</figure>"
        )
    return f'<figure class="figure">{img_tag}</figure>'


_HEADING_TAGS = ("h1", "h2", "h3")


def _heading_tag(level: int) -> str:
    """heading level(0~6)을 <h1>~<h3> 태그로 매핑한다.

    level 0(레벨 정보 없음, V1 호환)과 level 1은 <h1>. level 2는 <h2>.
    level 3 이상(소소제목)은 <h3>로 클램프한다.
    """
    if level <= 1:
        return "h1"
    idx = min(level - 1, len(_HEADING_TAGS) - 1)
    return _HEADING_TAGS[idx]


def markdown_table_to_html(md: str) -> str:
    """마크다운 파이프 표를 HTML <table>로 변환한다.

    Mistral OCR이 표를 마크다운으로 반환하는 것에 대응. 파이프 표 형식이
    아니면 <pre>로 폴백 (깨진 표라도 내용은 보존).
    """
    lines = [ln.strip() for ln in md.strip().split("\n") if ln.strip()]
    rows = [ln for ln in lines if ln.startswith("|")]
    if len(rows) < 2:
        return f"<pre>{_escape_html(md.strip())}</pre>"

    def cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip("|").split("|")]

    def is_separator(row: str) -> bool:
        return all(set(c) <= set("-: ") and c for c in cells(row))

    parts = ["<table>"]
    header_done = False
    for row in rows:
        if is_separator(row):
            continue
        tag = "td" if header_done else "th"
        tds = "".join(f"<{tag}>{to_xhtml(c)}</{tag}>" for c in cells(row))
        parts.append(f"<tr>{tds}</tr>")
        header_done = True
    parts.append("</table>")
    return "".join(parts)


def _escape_html(text: str) -> str:
    """HTML 특수문자를 이스케이프한다."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
