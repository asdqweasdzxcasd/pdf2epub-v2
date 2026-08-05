"""ebooklib 기반 EPUB3 조립"""

import colorsys
import logging
import re
from pathlib import Path
from uuid import uuid4

from ebooklib import epub

from app.pipeline.markdown_inline import to_xhtml
from app.pipeline.text_merge import merge_lines, split_soft_wrapped_paragraphs

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
# - body의 font-family는 특정 폰트를 강제하지 않는다 -- 리더에서 사용자가
#   고른 폰트/크기를 덮어쓰지 않기 위함. 단, generic 키워드인 "serif"는
#   예외로 허용한다: 원본 책이 명조(바탕) 계열로 조판돼 있어 이를 복원하되,
#   특정 폰트명을 못박지 않으므로 사용자가 리더에서 고른 serif 계열 폰트가
#   그대로 쓰인다(강제 폰트 아님). 모노스페이스가 필요한 pre/code에는 별도
#   지정한다.
# - 길이 단위는 전부 em/rem/%만 쓴다 (px 금지) -- 사용자가 글자 크기를
#   키워도 여백/테두리가 함께 커지도록.
# - 배경/글자색은 하드코딩하지 않는다. currentColor + rgba 투명도 오버레이로
#   최소한만 쓰고, 옅은 오버레이는 어두운 배경에서 안 보이므로 아래
#   @media (prefers-color-scheme: dark) 블록에서 밝은 계열로 뒤집는다.
# - 한국어 조판: line-height 1.7~1.8, word-break: keep-all(단어 중간 줄바꿈
#   방지), overflow-wrap: break-word(긴 URL 등 대비).
DEFAULT_CSS = """\
/* ============================================================
   전역 -- 본문 폰트는 generic serif만 지정한다 (특정 폰트 강제 아님).
   원본 지면과 대조한 결과 이 책은 명조(바탕) 계열로 조판돼 있어 이를
   근사하되, 리더에서 사용자가 고른 serif 폰트가 그대로 적용되게 한다.
   ============================================================ */
body {
  font-family: serif;
  line-height: 1.8;
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
   본문 문단 -- 원본 지면(캡처 이미지)과 대조한 결과 이 책은 첫 줄
   들여쓰기를 쓰지 않고 문단 간 여백으로 구분하며 양쪽 정렬을 쓴다.
   둘 중 하나만 써야 지저분하지 않으므로 여백 방식으로 통일한다.
   양쪽 정렬은 한글 단어가 중간에 끊기지 않도록 word-break: keep-all과
   함께 쓴다(전역 규칙에 이미 있음).
   ============================================================ */
p {
  text-indent: 0;
  margin: 0 0 1.0em;
  text-align: justify;
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
/* 표 캡션 -- 조판 관례상 표 위에 오므로 caption-side: top. 그림 캡션보다
   더 옅게 둬서 표 본문과 확실히 구분되게 한다. */
table caption {
  caption-side: top;
  font-size: 0.85em;
  text-align: center;
  margin-bottom: 0.5em;
  opacity: 0.75;
}

/* ============================================================
   figure.listing -- 코드/리스팅 캡션. 그림과 달리 캡션이 코드 "위"에
   오는 조판 관례를 따른다 (마크업 순서 자체가 figcaption -> pre).
   ============================================================ */
figure.listing {
  margin: 1.5em 0;
  page-break-inside: avoid;
}
figure.listing figcaption {
  font-size: 0.85em;
  margin-bottom: 0.5em;
  opacity: 0.75;
}
figure.listing pre {
  margin: 0;
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
   aside.tinted -- 페이지 이미지에서 추출한 배경색으로 원본 책의 강조 박스
   (제목 박스, 표지 등)를 복원한다. 배경색은 인라인 style로 지정되므로
   여기서는 안쪽 여백/모서리/위아래 간격만 담당한다.

   다크 모드 주의: 배경색은 원본 지면에서 읽은 밝은 파스텔 색 그대로
   인라인으로 박혀 있어, 리더가 화면 전체를 다크 테마로 뒤집어도 이
   배경만은 뒤집히지 않고 원래 밝은 색으로 남는다. 그런데 리더가 본문
   글자색은 테마에 맞춰 밝은 색으로 바꿔버리면 "밝은 배경 위에 밝은 글자"가
   되어 안 보이게 된다. 이를 막기 위해 박스 안 글자색을 테마와 무관하게
   항상 어둡게 고정한다 -- 리더가 색을 강제로 덮어써도 이 규칙이 우선하도록
   구체적으로(자손 셀렉터 *) 지정한다.
   ============================================================ */
.tinted {
  padding: 0.4em 0.9em;
  margin: 1.4em 0 0.9em;
  border-radius: 0.2em;
  border-left: 0.5em solid currentColor;
  page-break-inside: avoid;
}

/* 박스 안의 제목은 밴드 높이를 키우지 않게 위아래 여백을 줄인다 */
.tinted h1, .tinted h2, .tinted h3 {
  margin: 0.15em 0;
  padding: 0;
}

.tinted p:last-child {
  margin-bottom: 0;
}
.tinted, .tinted * {
  color: #1a1a1a;
}
.tinted p {
  text-indent: 0;
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

    _set_cover_if_present(book, chapters, figures_dir)

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


def _set_cover_if_present(
    book: "epub.EpubBook",
    chapters: list[tuple[str, list]],
    figures_dir: Path,
) -> None:
    """첫 챕터의 첫 블록이 표지 이미지(FIGURE)면 EPUB 표지 메타데이터로도
    등록한다 -- 리더가 서가/썸네일에서 표지를 인식하게 하기 위함.

    본문에도 같은 이미지가 그대로 한 번 더 렌더되지만(image_items로 등록된
    "images/<파일명>"), set_cover는 별도 uid/파일명("images/cover_<파일명>")
    으로 새 항목을 추가하므로 zip 엔트리 충돌 없이 중복 등록된다.
    """
    if not chapters:
        return
    _, first_chapter_layouts = chapters[0]
    if not first_chapter_layouts or not first_chapter_layouts[0].blocks:
        return
    first_block = first_chapter_layouts[0].blocks[0]
    if _block_type_str(first_block) != "figure" or not first_block.image_path:
        return

    cover_path = figures_dir / first_block.image_path
    if not cover_path.exists():
        return

    book.set_cover(f"images/cover_{first_block.image_path}", cover_path.read_bytes())


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
    r"""챕터의 XHTML 컨텐츠를 생성한다.

    block_type별 변환 규칙:
    - heading → <h1>/<h2>/<h3> (level 기준. 짧은 제목이라고 강등하지 않음 -
      이유는 _NOISE_HEADING_RE 자리의 주석 참고)
    - paragraph → <p>. Mistral 응답은 마크다운이므로 마크다운 문단 규칙을
      따른다 -- 빈 줄(`\n\s*\n`)만 문단 구분으로 보고 별개 <p>를 만든다.
      단일 줄바꿈은 원본 지면의 줄바꿈일 뿐이므로 같은 문단으로 이어붙인다
      (app.pipeline.text_merge.split_soft_wrapped_paragraphs, 조사로
      이어지면 공백 없이 그 외엔 공백 하나). aside/caption/footnote도
      같은 헬퍼(또는 단일 문단만 필요할 땐 merge_lines)를 써서 줄바꿈이
      문단으로 쪼개지지 않게 한다. CODE만 예외로 줄바꿈을 그대로 보존한다.
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
    - CAPTION 바로 다음이 TABLE(내용 있음) → 표 캡션으로 간주해 <table> 첫
      자식으로 <caption>을 넣는다 (조판 관례상 표 캡션은 표 위에 옴)
    - CAPTION 바로 다음이 CODE(내용 있음) → <figure class="listing">로 감싸
      <figcaption>을 <pre> 앞에 둔다 (리스트/코드 캡션도 위에 옴)
    - 짝 없는 figure → <figure class="figure"><img/></figure>
    - 짝 없는 caption → <p class="caption"> (대상 블록이 없거나 내용이
      비어 있으면 캡션 텍스트 유실을 막기 위해 이 폴백을 그대로 쓴다)
    - table/formula → 이미지가 있으면 <img>, 없으면 alt 텍스트
    - footnote → <div class="footnote">
    - page_header, page_footer → EPUB에서 제외
    - 배경색(Block.bg)이 있고 채널별 최대 차 8 이하로 비슷한, 같은 페이지
      안에서 연속된 텍스트 계열 블록들은 <aside class="tinted"
      style="background-color:#rrggbb"> 박스로 묶는다 (원본 책의 강조 박스
      복원). 페이지의 텍스트 계열 블록 전부가 같은 색이면 그건 박스가 아니라
      페이지 배경이므로 틴트를 적용하지 않는다. 페이지 경계를 넘어 묶지 않음.
      자세한 규칙은 _compute_tint_runs 참고.
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
        _render_page_with_tints(layout.blocks, parts)

    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


_TINT_TOL = 8  # 채널별 최대 차 이 값 이하면 "비슷한 배경색"으로 묶는다
_TINT_TYPES = frozenset({"heading", "paragraph", "list_item", "code", "caption"})


def _bg_close(a: tuple[int, int, int], b: tuple[int, int, int], tol: int = _TINT_TOL) -> bool:
    """두 배경색의 채널별 차가 모두 tol 이하인지."""
    return all(abs(a[k] - b[k]) <= tol for k in range(3))


def _compute_tint_runs(
    blocks: list,
) -> list[tuple[int, int, tuple[int, int, int]]]:
    """한 페이지의 블록 리스트에서 배경 틴트 박스로 묶을 (시작, 끝(배타),
    색) 구간들을 계산한다.

    가드: 텍스트 계열 블록(_TINT_TYPES) 전부가 같은 비-None 배경색이면 그건
    강조 박스가 아니라 페이지 배경이므로 빈 리스트를 반환한다(틴트 미적용).

    그 외에는 bg가 None이 아니고 서로 비슷한(≤_TINT_TOL) 색이 연속되는
    구간마다 하나의 런(run)을 만든다(단일 블록도 런이 될 수 있다 -- 예:
    제목 하나만 박스 처리된 경우).
    """
    text_blocks = [b for b in blocks if _block_type_str(b) in _TINT_TYPES]
    if text_blocks and all(
        b.bg is not None and _bg_close(b.bg, text_blocks[0].bg) for b in text_blocks
    ):
        return []  # 페이지 전체 배경 -- 박스 아님

    runs: list[tuple[int, int, tuple[int, int, int]]] = []
    n = len(blocks)
    i = 0
    while i < n:
        bg = getattr(blocks[i], "bg", None)
        if bg is None:
            i += 1
            continue
        j = i + 1
        while j < n:
            nbg = getattr(blocks[j], "bg", None)
            if nbg is None or not _bg_close(nbg, bg):
                break
            j += 1
        runs.append((i, j, bg))
        i = j
    return runs


_ACCENT_SAT_MIN = 0.45  # 강조 막대의 최소 채도 (파스텔 배경도 이 아래로 안 내려감)
_ACCENT_SAT_MULT = 3  # 원본 채도를 이만큼 끌어올린다
_ACCENT_LIGHT_MULT = 0.62  # 원본 밝기를 이만큼 낮춘다
_ACCENT_LIGHT_MAX = 0.55  # 강조 막대 밝기 상한


def _accent_color(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """틴트 박스 배경색에서 왼쪽 강조 막대 색을 만든다.

    각 채널에 같은 배율(예: 0.55)을 곱해 어둡게 하는 방식은 흰색에 가까운
    파스텔 배경(채도가 이미 낮음)에서는 채도가 그대로 낮게 유지돼 회색
    (#8b8283 등)이 되어버린다 -- 원본 책은 같은 색 계열의 선명한 막대를
    쓰므로 이 방식은 원본과 다르다.

    RGB -> HLS로 변환해 색상(hue)은 그대로 두고, 채도(saturation)는 크게
    끌어올리고(원본의 _ACCENT_SAT_MULT배, 최소 _ACCENT_SAT_MIN) 밝기
    (lightness)는 낮춰(원본의 _ACCENT_LIGHT_MULT배, 최대 _ACCENT_LIGHT_MAX)
    다시 RGB로 되돌린다.

    무채색(회색) 입력은 채도를 억지로 끌어올리면 안 되므로(존재하지 않는
    색상이 생김) 원본 채도 0을 그대로 유지하고 밝기만 낮춘다.
    """
    r, g, b = (c / 255 for c in rgb)
    h, lightness, saturation = colorsys.rgb_to_hls(r, g, b)

    new_lightness = min(lightness * _ACCENT_LIGHT_MULT, _ACCENT_LIGHT_MAX)
    if saturation <= 0:
        new_saturation = 0.0
    else:
        new_saturation = min(max(saturation * _ACCENT_SAT_MULT, _ACCENT_SAT_MIN), 1.0)

    nr, ng, nb = colorsys.hls_to_rgb(h, new_lightness, new_saturation)
    return tuple(
        min(255, max(0, round(c * 255))) for c in (nr, ng, nb)
    )


def _render_page_with_tints(blocks: list, parts: list) -> None:
    """페이지 블록들을 렌더링하되, _compute_tint_runs가 찾은 구간은
    <aside class="tinted"> 박스로 감싼다.

    bg가 설정된 블록이 없으면(V1 등 기존 호출부) runs가 비어 있어 페이지
    전체를 한 번에 _render_page_blocks로 넘긴다 -- 기존 동작과 동일.
    """
    runs = _compute_tint_runs(blocks)
    if not runs:
        _render_page_blocks(blocks, parts)
        return

    n = len(blocks)
    i = 0
    run_idx = 0
    while i < n:
        if run_idx < len(runs) and runs[run_idx][0] == i:
            start, end, color = runs[run_idx]
            run_idx += 1
            seg_start = len(parts)
            _render_page_blocks(blocks[start:end], parts)
            if len(parts) > seg_start:
                hex_color = "#%02x%02x%02x" % color
                # 원본 책은 강조 밴드 왼쪽에 같은 계열의 선명한 막대를 둔다.
                # 배경색과 채널별로 같은 비율로 어둡게 하면(예: *0.55) 파스텔
                # 배경(채도가 이미 낮음)에서는 채도가 그대로 낮아 회색이 되어
                # 버리므로, HLS 공간에서 채도를 끌어올리고 밝기를 낮춘다
                # (_accent_color 참고).
                accent = "#%02x%02x%02x" % _accent_color(color)
                wrapped = parts[seg_start:]
                del parts[seg_start:]
                parts.append(
                    f'<aside class="tinted" style="background-color:{hex_color};'
                    f'border-left-color:{accent}">'
                )
                parts.extend(wrapped)
                parts.append("</aside>")
            i = end
        else:
            next_boundary = runs[run_idx][0] if run_idx < len(runs) else n
            _render_page_blocks(blocks[i:next_boundary], parts)
            i = next_boundary


def _render_page_blocks(blocks: list, parts: list) -> None:
    """블록 리스트(페이지 전체 또는 틴트 박스로 묶일 부분범위) 하나를
    XHTML로 렌더링해 parts에 append한다.

    blocks가 페이지의 부분 슬라이스일 때는 슬라이스 경계 밖을 넘겨보는
    룩어헤드(예: figure+caption 병합, 연속 list_item 묶기)가 슬라이스 길이
    n으로 자연히 막힌다 -- 틴트 박스 경계를 넘는 병합을 방지한다.
    """
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
                    # 빈 줄만 문단 구분으로 본다. 단일 줄바꿈은 지면
                    # 줄바꿈일 뿐이므로 같은 문단으로 이어붙인다.
                    for para in split_soft_wrapped_paragraphs(text):
                        parts.append(f"<p>{to_xhtml(para)}</p>")

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
                    # paragraph와 동일한 규칙 -- 단일 줄바꿈은 같은 문단으로
                    # 이어붙이고, 빈 줄만 새 <p>로 나눈다.
                    inner_parts = [
                        f"<p>{to_xhtml(para)}</p>"
                        for para in split_soft_wrapped_paragraphs(text)
                    ]
                    if inner_parts:
                        parts.append('<aside class="memo">')
                        parts.extend(inner_parts)
                        parts.append("</aside>")

            elif bt == "figure" and block.image_path:
                next_block = blocks[i + 1] if i + 1 < n else None
                if next_block is not None and _block_type_str(next_block) == "caption":
                    caption_text = (
                        merge_lines(next_block.text) if next_block.text else ""
                    )
                    parts.append(_figure_html(block.image_path, caption_text))
                    i += 2
                    continue
                parts.append(_figure_html(block.image_path))

            elif bt == "table":
                if block.image_path or (block.text and block.text.strip()):
                    parts.append(_table_html(block))

            elif bt == "formula" and block.image_path:
                img_src = f"images/{block.image_path}"
                parts.append(
                    f'<div class="formula">'
                    f'<img src="{img_src}" alt="수식"/>'
                    f"</div>"
                )

            elif bt == "caption":
                next_block = blocks[i + 1] if i + 1 < n else None
                next_bt = _block_type_str(next_block) if next_block is not None else ""
                caption_text = merge_lines(block.text) if block.text else ""

                if next_bt == "figure" and next_block.image_path:
                    parts.append(_figure_html(next_block.image_path, caption_text))
                    i += 2
                    continue

                if next_bt == "table" and (next_block.image_path or (next_block.text and next_block.text.strip())):
                    parts.append(_table_html(next_block, caption_text))
                    i += 2
                    continue

                if next_bt == "code" and next_block.text and next_block.text.strip():
                    parts.append(_listing_html(next_block.text, caption_text))
                    i += 2
                    continue

                if caption_text:
                    parts.append(
                        f'<p class="caption">{to_xhtml(caption_text)}</p>'
                    )

            elif bt == "footnote":
                text = merge_lines(block.text) if block.text else ""
                if text:
                    parts.append(
                        f'<div class="footnote">'
                        f"<p>{to_xhtml(text)}</p>"
                        f"</div>"
                    )

            # page_header, page_footer는 EPUB에서 제외 (무시)

            i += 1


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


def _table_html(block, caption_text: str = "") -> str:
    """TABLE 블록을 렌더링한다. caption_text가 있으면 <table> 첫 자식으로
    <caption>을 넣는다 (HTML 표준 -- <caption>은 <table>의 direct child만
    허용됨). caption이 없으면 기존 렌더링(이미지는 <div class="table-img">,
    텍스트는 markdown_table_to_html)을 그대로 유지한다.
    """
    if block.image_path:
        img_src = f"images/{block.image_path}"
        if caption_text:
            return (
                f"<table><caption>{to_xhtml(caption_text)}</caption>"
                f'<tr><td><img src="{img_src}" alt="표"/></td></tr></table>'
            )
        return f'<div class="table-img"><img src="{img_src}" alt="표"/></div>'
    return markdown_table_to_html(block.text, caption_text)


def _listing_html(code_text: str, caption_text: str) -> str:
    """CODE 블록 + 앞선 CAPTION을 <figure class="listing">으로 감싼다.

    책 조판 관례상 표/리스팅 캡션은 대상 블록 "위"에 오므로 figcaption을
    <pre> 앞에 둔다 (그림 캡션과 반대 순서).
    """
    return (
        f'<figure class="listing"><figcaption>{to_xhtml(caption_text)}</figcaption>'
        f"{_code_html(code_text)}</figure>"
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


def markdown_table_to_html(md: str, caption_text: str = "") -> str:
    """마크다운 파이프 표를 HTML <table>로 변환한다.

    Mistral OCR이 표를 마크다운으로 반환하는 것에 대응. 파이프 표 형식이
    아니면 <pre>로 폴백 (깨진 표라도 내용은 보존). caption_text가 있으면
    <table> 첫 자식으로 <caption>을 넣는다 (HTML 표준).
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
    if caption_text:
        parts.append(f"<caption>{to_xhtml(caption_text)}</caption>")
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
