"""ebooklib 기반 EPUB3 조립"""

import logging
from pathlib import Path
from uuid import uuid4

from ebooklib import epub

from app.pipeline.markdown_inline import to_xhtml

logger = logging.getLogger(__name__)


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
        if not any(tag in html_content for tag in ("<h1>", "<p>", "<div", "<img")):
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
    css_content = """\
body { font-family: serif; line-height: 1.8; margin: 1em; }
h1, h2, h3 { font-family: sans-serif; margin-top: 1.5em; }
p { text-indent: 1em; margin: 0.5em 0; }
figure.figure { text-align: center; margin: 1em 0; }
figure.figure img { max-width: 100%; height: auto; }
figcaption { font-size: 0.9em; color: #555; font-style: italic; margin-top: 0.3em; }
.table-img { text-align: center; margin: 1em 0; }
.table-img img { max-width: 100%; height: auto; }
.formula { text-align: center; margin: 0.5em 0; }
.formula img { max-width: 80%; height: auto; }
.caption { font-size: 0.9em; color: #555; text-align: center; font-style: italic; }
.footnote { font-size: 0.85em; color: #666; border-top: 1px solid #ccc; padding-top: 0.5em; margin-top: 2em; }
table { border-collapse: collapse; margin: 1em auto; font-size: 0.9em; }
th, td { border: 1px solid #999; padding: 0.3em 0.6em; }
th { background: #eee; }
"""
    return epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=css_content.encode("utf-8"),
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
    - heading → <h1>
    - paragraph → <p> (줄바꿈 단위로 분리)
    - list_item → <p> 앞에 불릿 추가
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
        'xmlns:epub="http://www.idpf.org/2007/ops">',
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
                text = block.text.strip() if block.text else ""
                if text:
                    parts.append(
                        f"<p>* {to_xhtml(text)}</p>"
                    )

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
