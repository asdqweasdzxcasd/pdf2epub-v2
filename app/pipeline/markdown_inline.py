"""Mistral OCR이 반환하는 마크다운 인라인 표기를 XHTML로 변환한다.

Mistral OCR은 블록 content를 마크다운으로 반환한다 (`## 제목`, `**굵게**`,
`$^{5}$` 각주 등). 이를 이스케이프만 해서 XHTML에 그대로 넣으면 마크업이
텍스트로 그대로 노출된다. 이 모듈은:

- parse_heading: 헤딩 접두사(`#`~`######`)를 레벨과 본문으로 분리
- to_xhtml: 본문 텍스트의 인라인 마크다운을 HTML 태그로 변환 (HTML 특수문자
  이스케이프 포함)
"""

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.DOTALL)

_SUP_BRACED_RE = re.compile(r"\$\^\{([^{}$]+)\}\$")
_SUP_PLAIN_RE = re.compile(r"\$\^([^\s${}]+)\$")
_SUB_BRACED_RE = re.compile(r"\$_\{([^{}$]+)\}\$")
_DOLLAR_RE = re.compile(r"\$([^$]+)\$")
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")


def parse_heading(text: str) -> tuple[int, str]:
    """'## 제목' -> (2, '제목'). 헤딩 접두사가 없으면 (0, 원문)을 반환한다.

    `#` 뒤에 공백이 없으면(예: '#제목') 헤딩으로 보지 않는다.
    """
    match = _HEADING_RE.match(text)
    if not match:
        return 0, text
    level = len(match.group(1))
    title = match.group(2).strip()
    return level, title


def _escape_html(text: str) -> str:
    """HTML 특수문자를 이스케이프한다 (epub_build._escape_html과 동일 규칙)."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def to_xhtml(text: str) -> str:
    """마크다운 인라인 표기를 XHTML로 변환한다 (HTML 이스케이프 포함).

    순서: HTML 이스케이프 -> 위/아래첨자($...$) -> 일반 $...$ 제거 ->
    인라인 코드(백틱) -> 굵게(**) -> 기울임(*). `*`, `$`, 백틱은
    이스케이프에 영향받지 않으므로 순서를 바꿔도 안전하다.

    짝이 맞지 않는 마커(예: 닫는 `**` 없음)는 그대로 둔다 — 텍스트 손실 금지.
    `_밑줄_`은 변환하지 않는다 (snake_case 오변환 방지).
    """
    escaped = _escape_html(text)

    escaped = _SUP_BRACED_RE.sub(r"<sup>\1</sup>", escaped)
    escaped = _SUP_PLAIN_RE.sub(r"<sup>\1</sup>", escaped)
    escaped = _SUB_BRACED_RE.sub(r"<sub>\1</sub>", escaped)
    escaped = _DOLLAR_RE.sub(r"\1", escaped)

    escaped = _CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)

    return escaped
