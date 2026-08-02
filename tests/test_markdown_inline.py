"""Mistral OCR 마크다운 인라인 표기 → XHTML 변환 테스트"""
from app.pipeline.markdown_inline import parse_heading, to_xhtml

# --- parse_heading ---


def test_h1_파싱():
    assert parse_heading("# 제목") == (1, "제목")


def test_h2_파싱():
    assert parse_heading("## 정적 자원과 브라우저 캐시") == (2, "정적 자원과 브라우저 캐시")


def test_h6까지_파싱():
    assert parse_heading("###### 여섯단계") == (6, "여섯단계")


def test_접두사_없으면_레벨0_원문반환():
    assert parse_heading("그냥 본문") == (0, "그냥 본문")


def test_샵만_있고_공백없으면_헤딩아님():
    assert parse_heading("#제목") == (0, "#제목")


def test_헤딩_텍스트_앞뒤_공백_제거():
    assert parse_heading("###   여백 있음   ") == (3, "여백 있음")


# --- to_xhtml: bold ---


def test_굵게_변환():
    assert to_xhtml("**굵게**") == "<strong>굵게</strong>"


def test_굵게_문장중간():
    assert to_xhtml("이것은 **중요** 합니다") == "이것은 <strong>중요</strong> 합니다"


def test_굵게_짝안맞으면_그대로():
    # 닫는 ** 없음 -> 텍스트 손실 없이 그대로 보존
    assert to_xhtml("**닫히지않음") == "**닫히지않음"


# --- to_xhtml: code ---


def test_코드_변환():
    assert to_xhtml("`코드`") == "<code>코드</code>"


# --- to_xhtml: sup/sub ---


def test_위첨자_중괄호():
    assert to_xhtml("$^{5}$") == "<sup>5</sup>"


def test_위첨자_중괄호없음():
    assert to_xhtml("$^5$") == "<sup>5</sup>"


def test_아래첨자():
    assert to_xhtml("$_{n}$") == "<sub>n</sub>"


def test_각주표시_문장중간():
    assert to_xhtml("본문 내용$^{5}$ 계속") == "본문 내용<sup>5</sup> 계속"


def test_일반_달러표기는_달러만_제거():
    assert to_xhtml("$x + y$") == "x + y"


# --- to_xhtml: italic (single asterisk) ---


def test_기울임_변환():
    assert to_xhtml("*기울임*") == "<em>기울임</em>"


def test_밑줄은_변환하지_않음():
    # snake_case 오변환 방지 - _underline_ 은 그대로 둔다
    assert to_xhtml("_snake_case_") == "_snake_case_"


def test_굵게와_기울임_충돌없음():
    assert to_xhtml("**굵게** 그리고 *기울임*") == "<strong>굵게</strong> 그리고 <em>기울임</em>"


# --- to_xhtml: HTML escape ---


def test_html_이스케이프():
    assert to_xhtml("<script>alert(1)</script>") == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )


def test_이스케이프와_마크업_동시적용():
    assert to_xhtml("**<script>위험</script>**") == (
        "<strong>&lt;script&gt;위험&lt;/script&gt;</strong>"
    )


def test_따옴표_이스케이프():
    assert to_xhtml('"인용"') == "&quot;인용&quot;"


# --- heading 접두사는 to_xhtml 책임이 아니다 (parse_heading이 이미 벗김) ---


def test_헤딩접두사가_섞인_텍스트도_손실없이_보존():
    # to_xhtml은 heading 접두사를 모른다 -- 별도 처리 없이 그대로 이스케이프만
    assert to_xhtml("## 정적 자원") == "## 정적 자원"
