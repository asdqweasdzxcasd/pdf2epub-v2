"""블록 텍스트 안의 단일 줄바꿈(연식 줄바꿈) 처리 테스트.

Mistral OCR은 한 문단 전체를 블록 하나로 주되, 원본 지면의 줄바꿈을
그대로 `\n`으로 보존한다(예: "...Apache HttpClient\n는 소켓 타임아웃을...").
이는 마크다운 문단 규칙과 같다 -- 빈 줄만 진짜 문단 구분이고, 단일 줄바꿈은
지면 줄바꿈일 뿐이므로 같은 문단으로 이어붙여야 한다. 이 모듈은
app.pipeline.text_merge의 이어붙이기 헬퍼(merge_text,
split_soft_wrapped_paragraphs, merge_lines)를 검증한다.

--- 이 파일이 예전에 테스트하던 "연속 블록 병합"(_merge_paragraph_blocks)은
제거되었다 ---

예전 진단은 "Mistral이 한 문단을 여러 블록으로 쪼갠다"였고, 그 블록들을
휴리스틱(문장 미종결 + 조사/소문자 시작)으로 다시 합치려 했다. 실측
캐시(../ebook-converter/testdata/ocr-cache, 여러 권 분량)에 그 휴리스틱을
그대로 적용해 검출된 36건을 전부 육안 검토한 결과, 진짜 문단 연속은 단
한 건도 없었고 전부 오탐이었다:
- 색인 페이지의 표제어들("Event-Driven Architecture 310" 다음
  "eventual consistency 133" 같은, 알파벳순으로 나열된 서로 무관한 항목)
- text로 오분류된 코드 줄들(Java/SQL 코드 라인이 순서대로 하나의
  "문단"으로 합쳐짐 -- 코드 구조가 깨짐)
- TOC/제목처럼 보이는 짧고 무관한 줄들
그래서 블록 간 병합 기능 자체를 제거했다. 진짜 원인(지면 줄바꿈)은
app/pipeline/epub_build.py 렌더링 단계에서 처리한다(아래 테스트 참고).
"""
from pathlib import Path

from app.pipeline.layout import BlockType
from app.pipeline.ocr_layout import build_layouts_from_ocr
from app.pipeline.text_merge import merge_lines, merge_text, split_soft_wrapped_paragraphs


def _page(blocks: list[dict], page_index: int = 0) -> dict:
    return {"index": page_index, "dimensions": {}, "markdown": "", "blocks": blocks}


def _text_block(content: str) -> dict:
    return {
        "type": "text",
        "top_left_x": 0.0, "top_left_y": 0.0,
        "bottom_right_x": 0.5, "bottom_right_y": 0.1,
        "content": content,
    }


# ---------------------------------------------------------------------------
# merge_text 단위 테스트
# ---------------------------------------------------------------------------

def test_조사로_시작하면_공백_없이_병합():
    merged = merge_text(
        "읽기 타임아웃을 지정할 때는 실제로 설정하는 값이 무엇인지 확인해야 한다. "
        "예를 들어 Apache HttpClient",
        "는 소켓 타임아웃을 설정한다.",
    )
    assert "HttpClient는 소켓" in merged
    assert merged == (
        "읽기 타임아웃을 지정할 때는 실제로 설정하는 값이 무엇인지 확인해야 한다. "
        "예를 들어 Apache HttpClient는 소켓 타임아웃을 설정한다."
    )


def test_소문자_라틴_시작은_공백_하나로_병합():
    merged = merge_text("이 코드는 다음과 같이 동작하는데", "http 요청을 보낸다.")
    assert merged == "이 코드는 다음과 같이 동작하는데 http 요청을 보낸다."


def test_일반_명사_시작도_공백_하나로_병합():
    merged = merge_text("이어지지 않은 문장 예시", "서버는 응답을 반환한다.")
    assert merged == "이어지지 않은 문장 예시 서버는 응답을 반환한다."


# ---------------------------------------------------------------------------
# split_soft_wrapped_paragraphs -- 실측 사례
# ---------------------------------------------------------------------------

def test_실측_사례_단일_줄바꿈_세개는_한_문단이_된다():
    """버그 리포트 실측 캐시에서 그대로 가져온 사례: 원본 지면 줄바꿈 3개가
    있는 블록 하나가 <p> 4개로 쪼개지던 버그. 이제 <p> 1개가 되어야 한다."""
    text = (
        "읽기 타임아웃을 지정할 때는 ... 예를 들어 Apache HttpClient\n"
        "는 소켓 타임아웃을 설정한다. ... 전체 응답 시간에 대한\n"
        "타임아웃을 의미하지는 않는다. ... 5초 이상 걸릴 수\n"
        "있다."
    )
    paragraphs = split_soft_wrapped_paragraphs(text)
    assert len(paragraphs) == 1
    assert "HttpClient는 소켓" in paragraphs[0]  # 조사 병합: 공백 없음
    assert "걸릴 수 있다" in paragraphs[0]  # 일반 이어붙임: 공백 있음
    assert "\n" not in paragraphs[0]


def test_빈_줄로_구분된_텍스트는_두_문단이_된다():
    text = "첫 번째 문단이다.\n\n두 번째 문단이다."
    paragraphs = split_soft_wrapped_paragraphs(text)
    assert paragraphs == ["첫 번째 문단이다.", "두 번째 문단이다."]


def test_여러_빈_줄도_한_번의_문단_구분으로_취급된다():
    text = "첫 번째 문단이다.\n\n\n\n두 번째 문단이다."
    paragraphs = split_soft_wrapped_paragraphs(text)
    assert paragraphs == ["첫 번째 문단이다.", "두 번째 문단이다."]


def test_빈_텍스트는_빈_리스트():
    assert split_soft_wrapped_paragraphs("") == []


# ---------------------------------------------------------------------------
# merge_lines -- caption/footnote처럼 항상 태그 하나인 텍스트용
# ---------------------------------------------------------------------------

def test_merge_lines는_모든_줄을_하나로_합친다():
    text = "그림 5.1 이벤트\n루프 동작 방식"
    assert merge_lines(text) == "그림 5.1 이벤트 루프 동작 방식"


def test_merge_lines는_빈_줄도_무시하고_합친다():
    text = "각주 첫\n\n번째 줄"
    assert merge_lines(text) == "각주 첫 번째 줄"


# ---------------------------------------------------------------------------
# build_layouts_from_ocr 회귀 -- 블록 간 병합은 더 이상 없다
# ---------------------------------------------------------------------------

def test_build_layouts_연속_블록은_더이상_병합되지_않는다(tmp_path: Path):
    """예전 휴리스틱이면 병합 대상으로 오판했을 두 블록(조사 후보로 시작)도
    이제는 그대로 별개 블록으로 남아야 한다 -- 블록 간 병합 기능 제거."""
    pages = [_page([
        _text_block("이것은 여러 줄로 쪼개진 문장인데 아직 끝나지"),
        _text_block("는 계속 이어지다가 아직 안 끝나고"),
    ])]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)

    assert len(layouts) == 1
    assert len(layouts[0].blocks) == 2


def test_build_layouts_실측_오탐_사례_색인_표제어는_병합되지_않는다(tmp_path: Path):
    """실측 캐시에서 발견된 오탐 사례: 색인 페이지의 알파벳순 표제어들이
    예전 휴리스틱으로는 병합 대상이었다("eventual"이 소문자 라틴 시작이므로).
    서로 무관한 항목이므로 병합되면 안 된다."""
    pages = [_page([
        _text_block("Event-Driven Architecture 310"),
        _text_block("eventual consistency 133"),
    ])]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)

    assert len(layouts) == 1
    assert len(layouts[0].blocks) == 2
    assert layouts[0].blocks[0].text == "Event-Driven Architecture 310"
    assert layouts[0].blocks[1].text == "eventual consistency 133"


def test_build_layouts_헤딩_사이에_끼어도_그대로다(tmp_path: Path):
    pages = [_page([
        _text_block("끝나지 않은 문단"),
        {"type": "title", "top_left_x": 0.0, "top_left_y": 0.0,
         "bottom_right_x": 0.5, "bottom_right_y": 0.1, "content": "## 는 제목처럼 보이는 헤딩"},
    ])]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)

    assert len(layouts) == 1
    assert len(layouts[0].blocks) == 2
    assert layouts[0].blocks[1].block_type is BlockType.HEADING


def test_build_layouts_실측_픽스처_회귀(tmp_path: Path):
    import json

    fixture = Path(__file__).parent / "fixtures" / "mistral_response_sample.json"
    pages = json.loads(fixture.read_text())["pages"]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)
    assert len(layouts) == 2
