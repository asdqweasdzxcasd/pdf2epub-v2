"""OCR이 줄 단위로 쪼갠 문단을 병합하는 로직 테스트.

Mistral OCR이 한 문단을 여러 PARAGRAPH 블록으로 나눠 반환하는 경우
(실측: 연속 text 블록 쌍 1140개 중 124개, 11%) 렌더링에서 문단이
조사로 시작하는 등 부자연스럽게 끊긴다. build_layouts_from_ocr가
연속된 PARAGRAPH 블록을 보수적 조건 하에 병합해야 한다.

병합 판정 규칙 (셋 다 만족해야 병합):
1. 앞뒤 블록이 모두 BlockType.PARAGRAPH이고 bg가 같다.
2. 앞 블록이 문장 종결 부호로 끝나지 않는다.
3. 뒤 블록이 "이어짐 신호"로 시작한다 — 조사 후보 글자 + 그 다음 글자가
   공백인 경우(예: "는 소켓"), 또는 소문자 라틴 문자로 시작하는 경우.

주의: 조사 후보 글자 뒤에 공백이 오는지만 보는 문자 단위 판정이라
"이 책은…"처럼 실제로는 새 문장이 시작되는 경우도 오탐으로 병합될 수 있다
(아래 test_오탐_한계_* 참고). 보수적으로 설계했어도 완벽한 판별은 아니다.
"""
from pathlib import Path

from app.pipeline.layout import Block, BlockType
from app.pipeline.ocr_layout import _merge_text, _should_merge, build_layouts_from_ocr


def _para(text: str, bg=(255, 255, 255)) -> Block:
    return Block(block_type=BlockType.PARAGRAPH, bbox=(0.0, 0.0, 1.0, 1.0),
                 confidence=1.0, text=text, bg=bg)


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
# _should_merge / _merge_text 단위 테스트
# ---------------------------------------------------------------------------

def test_실측_사례_조사로_이어지는_문장은_병합된다():
    prev = _para("읽기 타임아웃을 지정할 때는 실제로 설정하는 값이 무엇인지 확인해야 한다. "
                 "예를 들어 Apache HttpClient")
    nxt = _para("는 소켓 타임아웃을 설정한다.")
    assert _should_merge(prev, nxt) is True
    merged = _merge_text(prev.text, nxt.text)
    assert "HttpClient는 소켓" in merged
    # 조사 병합은 공백 없이 붙는다
    assert merged == prev.text + nxt.text


def test_3연속_병합():
    b1 = _para("이것은 여러 줄로 쪼개진 문장인데 아직 끝나지")
    b2 = _para("는 계속 이어지다가 아직 안 끝나고")
    b3 = _para("도 여기서 마침내 끝난다.")

    assert _should_merge(b1, b2) is True
    merged_12_text = _merge_text(b1.text, b2.text)
    b12 = _para(merged_12_text)

    assert _should_merge(b12, b3) is True
    merged_123_text = _merge_text(merged_12_text, b3.text)

    # 조사 병합은 공백 없이 이어붙으므로 3개 원문의 단순 연결과 같다
    assert merged_123_text == b1.text + b2.text + b3.text


def test_앞_블록이_문장_종결_부호로_끝나면_병합_안함():
    prev = _para("이 문장은 마침표로 끝난다.")
    nxt = _para("는 소켓 타임아웃을 설정한다.")
    assert _should_merge(prev, nxt) is False


def test_뒤_블록이_조사_아닌_일반_명사로_시작하면_병합_안함():
    # "서"는 조사 후보 목록에 있지만, 바로 뒤 글자가 공백이 아니므로
    # (서버는 -> '서' 다음 글자가 '버') 이어짐 신호로 보지 않는다.
    prev = _para("이어지지 않은 문장 예시")
    nxt = _para("서버는 응답을 반환한다.")
    assert _should_merge(prev, nxt) is False


def test_조사_후보_뒤에_공백이_오면_병합():
    # "도 " 처럼 조사 후보 글자 뒤에 공백이 오면 (실제로는 부사/다른 단어의
    # 시작이라도) 규칙상 병합 대상이다.
    prev = _para("아직 끝나지 않은 문장")
    nxt = _para("도 마찬가지로 이 규칙을 적용한다.")
    assert _should_merge(prev, nxt) is True


def test_오탐_한계_이_책은_처럼_조사후보_뒤_공백은_병합된다():
    """한계 문서화: '이 책은...'처럼 첫 글자가 조사 후보('이')이고 두 번째
    문자가 공백이면, 실제로는 새 문장의 시작(관형사 '이')이라도 규칙상
    병합 대상으로 판정된다. 보수적 규칙이라도 완벽하지 않다는 것을
    테스트로 명시해 둔다."""
    prev = _para("아직 끝나지 않은 문장")
    nxt = _para("이 책은 새로운 문장의 시작이다.")
    assert _should_merge(prev, nxt) is True


def test_소문자_라틴_시작은_공백_하나로_병합():
    prev = _para("이 코드는 다음과 같이 동작하는데")
    nxt = _para("http 요청을 보낸다.")
    assert _should_merge(prev, nxt) is True
    merged = _merge_text(prev.text, nxt.text)
    assert merged == "이 코드는 다음과 같이 동작하는데 http 요청을 보낸다."


def test_대문자_라틴_시작은_병합_안함():
    prev = _para("이 코드는 다음과 같이 동작하는데")
    nxt = _para("HTTP 요청을 보낸다.")
    assert _should_merge(prev, nxt) is False


def test_HEADING과는_병합_안함():
    prev = _para("끝나지 않은 문장")
    heading = Block(block_type=BlockType.HEADING, bbox=(0, 0, 1, 1), confidence=1.0,
                     text="는 제목이다", level=1, bg=(255, 255, 255))
    assert _should_merge(prev, heading) is False


def test_CODE와는_병합_안함():
    prev = _para("끝나지 않은 문장")
    code = Block(block_type=BlockType.CODE, bbox=(0, 0, 1, 1), confidence=1.0,
                 text="는 코드 블록이다", bg=(255, 255, 255))
    assert _should_merge(prev, code) is False


def test_LIST_ITEM과는_병합_안함():
    prev = _para("끝나지 않은 문장")
    item = Block(block_type=BlockType.LIST_ITEM, bbox=(0, 0, 1, 1), confidence=1.0,
                 text="는 목록 항목이다", bg=(255, 255, 255))
    assert _should_merge(prev, item) is False


def test_bg가_다르면_병합_안함():
    prev = _para("끝나지 않은 문장", bg=(255, 255, 255))
    nxt = _para("는 다른 배경의 문단이다.", bg=(240, 240, 240))
    assert _should_merge(prev, nxt) is False


def test_bg가_둘다_None이면_병합된다():
    prev = _para("끝나지 않은 문장", bg=None)
    nxt = _para("는 배경 없는 문단이다.", bg=None)
    assert _should_merge(prev, nxt) is True


# ---------------------------------------------------------------------------
# build_layouts_from_ocr 통합 테스트
# ---------------------------------------------------------------------------

def test_build_layouts_실측_사례_두_블록이_한_문단으로_병합된다(tmp_path: Path):
    pages = [_page([
        _text_block("읽기 타임아웃을 지정할 때는 실제로 설정하는 값이 무엇인지 확인해야 한다. "
                    "예를 들어 Apache HttpClient"),
        _text_block("는 소켓 타임아웃을 설정한다. 소켓 타임아웃은 네트워크 패킷 단위를 기준으로 "
                    "하므로, 전체 응답 시간에 대한 타임아웃을 의미하지는 않는다."),
    ])]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)

    assert len(layouts) == 1
    blocks = layouts[0].blocks
    assert len(blocks) == 1
    assert "HttpClient는 소켓" in blocks[0].text


def test_build_layouts_3연속_텍스트_블록을_병합한다(tmp_path: Path):
    pages = [_page([
        _text_block("이것은 여러 줄로 쪼개진 문장인데 아직 끝나지"),
        _text_block("는 계속 이어지다가 아직 안 끝나고"),
        _text_block("도 여기서 마침내 끝난다."),
    ])]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)

    assert len(layouts) == 1
    blocks = layouts[0].blocks
    assert len(blocks) == 1
    assert blocks[0].text == (
        "이것은 여러 줄로 쪼개진 문장인데 아직 끝나지"
        "는 계속 이어지다가 아직 안 끝나고"
        "도 여기서 마침내 끝난다."
    )


def test_build_layouts_종결된_문단은_병합하지_않는다(tmp_path: Path):
    pages = [_page([
        _text_block("첫 번째 문단은 마침표로 끝난다."),
        _text_block("두 번째 문단도 독립적인 문장이다."),
    ])]
    layouts = build_layouts_from_ocr(pages, page_images=[], figures_dir=tmp_path)

    assert len(layouts) == 1
    assert len(layouts[0].blocks) == 2


def test_build_layouts_헤딩_사이에_끼면_병합_안함(tmp_path: Path):
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
