r"""블록 텍스트 안의 단일 줄바꿈(연식 줄바꿈)을 이어붙이는 공용 헬퍼.

Mistral OCR은 한 문단 전체를 블록 하나로 주되, 원본 지면의 줄바꿈을 그대로
`\n`으로 보존한다(예: "...Apache HttpClient\n는 소켓 타임아웃을..."). 이는
마크다운 문단 규칙과 같다 -- 빈 줄(`\n\s*\n`)만 진짜 문단 구분이고, 단일
줄바꿈은 지면 줄바꿈일 뿐이므로 같은 문단으로 이어붙여야 한다.

이 모듈은 원래 app/pipeline/ocr_layout.py에서 "OCR이 서로 다른 블록으로
잘못 쪼갠 문단"을 이어붙이는 용도(_merge_text)로 만들어졌다. 실측 결과
그 블록 간 병합(_merge_paragraph_blocks)은 색인 페이지의 표제어, 코드로
오분류된 텍스트 블록 등을 오탐으로 병합하는 문제만 있었고 실제로 필요한
경우는 찾지 못해 제거되었다(자세한 근거는 커밋 메시지 참고). 대신 진짜
원인이었던 "블록 하나 안의 줄바꿈 처리"가 필요해져, 이어붙이기 로직만
공용 위치인 이 모듈로 옮겨 app/pipeline/epub_build.py에서 재사용한다.
"""
import re

# 한국어 조사/어미 후보 -- 뒤 텍스트가 이 글자로 시작하면 앞 단어에 붙는
# 조사로 보고 공백 없이 이어붙인다(예: "는 소켓" -> "HttpClient는 소켓").
JOSA_CHARS = frozenset("는은이가를을에의도로와과만라며고서나든")

# 빈 줄(공백만 있는 줄 포함) -- 마크다운 문단 구분자.
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _starts_with_josa_candidate(text: str) -> bool:
    """text가 조사 후보 글자로 시작하고, 그 다음 글자가 공백인지(또는 글자가
    하나뿐인지) 본다 -- 이 경우만 "앞 단어에 붙는 조사"로 본다.

    다음 글자가 공백이 아니면(예: "서버는"의 "서") 조사가 아니라 그 글자로
    시작하는 다른 단어이므로 공백을 넣어야 한다. 문자 단위 판정이라
    "이 책은..."처럼 실제로는 새 문장이 시작하는 경우도 오탐으로 붙을 수
    있다 -- 보수적으로 설계했어도 완벽한 판별은 아니다.
    """
    if not text or text[0] not in JOSA_CHARS:
        return False
    return len(text) == 1 or text[1] == " "


def merge_text(prev_text: str, next_text: str) -> str:
    """두 줄의 텍스트를 이어붙인다.

    뒤 텍스트가 한글 조사 후보로 시작하고 그 다음 글자가 공백이면(또는
    글자가 하나뿐이면) 공백 없이 붙이고(앞 단어에 붙는 조사이므로), 그 외
    (소문자 라틴으로 끊긴 영문 단어, 조사 후보로 시작하지만 뒤에 다른
    글자가 바로 이어지는 일반 단어 등)에는 공백 하나를 넣어 붙인다.
    """
    prev = prev_text.rstrip()
    nxt = next_text.lstrip()
    if _starts_with_josa_candidate(nxt):
        return prev + nxt
    return prev + " " + nxt


def _merge_lines_seq(lines: list[str]) -> str:
    """줄 목록을 merge_text로 순차 병합한다. 빈 목록이면 빈 문자열."""
    if not lines:
        return ""
    merged = lines[0]
    for line in lines[1:]:
        merged = merge_text(merged, line)
    return merged


def split_soft_wrapped_paragraphs(text: str) -> list[str]:
    """블록 텍스트를 빈 줄 기준으로 문단 단위로 나누고, 각 문단 안의 단일
    줄바꿈은 merge_text로 이어붙인다.

    빈 문단(빈 줄만 있던 구간)은 결과에서 제외한다.
    """
    if not text:
        return []
    paragraphs: list[str] = []
    for group in _BLANK_LINE_RE.split(text):
        lines = [ln.strip() for ln in group.split("\n") if ln.strip()]
        merged = _merge_lines_seq(lines)
        if merged:
            paragraphs.append(merged)
    return paragraphs


def merge_lines(text: str) -> str:
    """텍스트 전체(빈 줄 포함 모든 줄)를 하나의 문자열로 이어붙인다.

    caption/footnote처럼 항상 태그 하나로만 렌더링되는 텍스트에 쓴다 --
    문단 구분을 별도로 표현할 곳이 없으므로 빈 줄 여부와 무관하게 모든
    줄을 순차 병합한다.
    """
    if not text:
        return ""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return _merge_lines_seq(lines)
