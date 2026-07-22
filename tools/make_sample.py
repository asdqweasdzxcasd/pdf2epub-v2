"""저작권 프리 데모용 샘플 PDF 생성기.

`samples/sample.pdf`를 재현 가능하게 만든다. 내용은 전부 본 작업을 위해 새로
작문한 가상의 "커피 로스팅 입문" 튜토리얼(한국어 본문 + 영어 용어 혼합)이며,
실제 서적·기사·웹 텍스트를 복사한 부분은 없다.

3페이지 구성:
    1p. 장 제목 + 본문 2문단
    2p. 본문 + 사각형 3개(공정 단계) + 연결 화살표 + 상자별 라벨 + 캡션
    3p. 본문 + 표(3열×4행 셀 그리드, 선으로 직접 그림)

생성 절차:
    1. 벡터/텍스트 레이어로 3페이지를 그린다 (insert_htmlbox 사용 — 한글+영문
       혼용 시 글자 간격이 자연스러운 MuPDF 폰트 폴백 경로).
    2. 각 페이지를 200 DPI로 픽스맵 렌더링한다.
    3. 렌더 이미지만으로 새 PDF를 재조립한다 (텍스트 레이어 없음 — 스캔본과
       동일 조건이어야 `--ocr api` 경로가 발동한다).

사용:
    .venv/bin/python tools/make_sample.py
    (기본 출력: samples/sample.pdf. --out 으로 변경 가능)
"""

import argparse
from pathlib import Path

import fitz  # PyMuPDF

PAGE_W, PAGE_H = 595, 842  # A4, pt
MARGIN = 56
RENDER_DPI = 200

BODY_CSS = "font-family: sans-serif; font-size: 12px; line-height: 1.6;"
TITLE_CSS = "font-family: sans-serif; font-size: 22px; font-weight: bold;"
CAPTION_CSS = "font-family: sans-serif; font-size: 10px; color: #444444;"
LABEL_CSS = "font-family: sans-serif; font-size: 11px; text-align: center;"
CELL_CSS = "font-family: sans-serif; font-size: 11px;"


# ---- 자체 작문 콘텐츠 (가상의 "커피 로스팅 입문" 튜토리얼) ----------------

TITLE = "1장. 커피 로스팅 입문"

PARA_1 = (
    "커피 로스팅은 생두(green bean)를 열로 가공하여 향미를 이끌어내는 과정이다. "
    "로스팅 과정 중 원두는 수분을 잃고 부피가 팽창하며, 당과 아미노산이 반응하는 "
    "마이야르(Maillard) 반응을 거쳐 짙은 갈색을 띠게 된다. 로스팅 온도와 시간의 "
    "조합에 따라 신맛, 단맛, 쓴맛의 균형이 달라지므로, 로스터는 원두의 산지와 "
    "품종에 맞춰 프로파일을 세심하게 설계해야 한다."
)

PARA_2 = (
    "이 튜토리얼에서는 로스팅의 세 단계 — 건조(drying), 마이야르 반응(Maillard "
    "reaction), 디벨롭먼트(development) — 를 순서대로 살펴보고, 각 단계에서 "
    "원두 내부와 외부에 일어나는 변화를 간단한 다이어그램으로 정리한다. 이어서 "
    "로스팅 강도에 따른 대표적인 세 등급 — 라이트, 미디엄, 다크 — 의 특징을 "
    "표로 비교한다. 실제 로스팅 기계나 특정 브랜드의 레시피를 다루지는 않으며, "
    "어디까지나 개념을 익히기 위해 새로 작성한 가상의 예시임을 밝혀둔다."
)

PAGE2_BODY = (
    "로스팅 과정은 흔히 세 단계로 나뉜다. 첫 단계인 건조 단계에서는 생두에 "
    "남아있는 수분이 증발하며 색이 옅은 노란빛으로 변한다. 이어지는 마이야르 "
    "단계에서는 당과 아미노산의 갈변 반응이 활발해지며 특유의 고소한 향이 "
    "형성되기 시작한다. 마지막 디벨롭먼트 단계에서는 원두 내부 압력이 높아지며 "
    "'크랙(crack)' 소리와 함께 향미가 완성된다. 아래 그림은 이 흐름을 단순화하여 "
    "나타낸 것이다."
)

DIAGRAM_CAPTION = "그림 1. 로스팅 3단계 공정 흐름도 (본 자료를 위해 새로 작성한 가상 예시)"

DIAGRAM_BOXES = [
    ("건조", "Drying"),
    ("마이야르", "Maillard"),
    ("디벨롭먼트", "Development"),
]

PAGE3_BODY = (
    "로스팅 강도는 원두 표면 색상과 내부 온도로 구분하며, 흔히 라이트·미디엄· "
    "다크의 세 등급으로 나눈다. 아래 표는 각 등급의 대략적인 온도대와 맛 특징을 "
    "정리한 것으로, 특정 로스터리의 실측치가 아니라 이해를 돕기 위해 임의로 "
    "구성한 예시 수치다."
)

TABLE_CAPTION = "표 1. 로스팅 등급별 온도대와 맛 특징 (예시 수치)"

TABLE_ROWS = [
    ("등급", "온도대(°C)", "맛 특징"),
    ("라이트", "195~205", "밝은 산미, 옅은 바디"),
    ("미디엄", "205~220", "단맛과 산미의 균형"),
    ("다크", "220~230", "진한 바디, 쓴맛 강조"),
]


def _draw_arrow(shape: fitz.Shape, p_from: fitz.Point, p_to: fitz.Point) -> None:
    """p_from -> p_to 방향 직선 화살표 (선 + 채운 삼각형 화살촉)."""
    shape.draw_line(p_from, p_to)
    shape.finish(color=(0, 0, 0), width=1.5)

    # 화살촉: p_to를 꼭짓점으로 하는 작은 삼각형
    dx, dy = p_to.x - p_from.x, p_to.y - p_from.y
    length = max((dx**2 + dy**2) ** 0.5, 1e-6)
    ux, uy = dx / length, dy / length  # 진행 방향 단위벡터
    nx, ny = -uy, ux  # 법선 벡터

    head_len, head_w = 8, 5
    tip = p_to
    base_center = fitz.Point(p_to.x - ux * head_len, p_to.y - uy * head_len)
    left = fitz.Point(base_center.x + nx * head_w, base_center.y + ny * head_w)
    right = fitz.Point(base_center.x - nx * head_w, base_center.y - ny * head_w)
    shape.draw_polyline([tip, left, right, tip])
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0.5)


def build_page1(page: fitz.Page) -> None:
    page.insert_htmlbox(
        fitz.Rect(MARGIN, 60, PAGE_W - MARGIN, 100),
        f'<p style="{TITLE_CSS}">{TITLE}</p>',
    )
    page.insert_htmlbox(
        fitz.Rect(MARGIN, 120, PAGE_W - MARGIN, 320),
        f'<p style="{BODY_CSS}">{PARA_1}</p>',
    )
    page.insert_htmlbox(
        fitz.Rect(MARGIN, 340, PAGE_W - MARGIN, 560),
        f'<p style="{BODY_CSS}">{PARA_2}</p>',
    )


def build_page2(page: fitz.Page) -> None:
    page.insert_htmlbox(
        fitz.Rect(MARGIN, 60, PAGE_W - MARGIN, 220),
        f'<p style="{BODY_CSS}">{PAGE2_BODY}</p>',
    )

    shape = page.new_shape()

    box_w, box_h = 120, 60
    gap = 40
    total_w = len(DIAGRAM_BOXES) * box_w + (len(DIAGRAM_BOXES) - 1) * gap
    start_x = (PAGE_W - total_w) / 2
    y0 = 280

    box_rects = []
    for i in range(len(DIAGRAM_BOXES)):
        x0 = start_x + i * (box_w + gap)
        rect = fitz.Rect(x0, y0, x0 + box_w, y0 + box_h)
        box_rects.append(rect)
        shape.draw_rect(rect)
        shape.finish(color=(0, 0, 0), fill=(0.93, 0.93, 0.93), width=1.2)

    for i in range(len(box_rects) - 1):
        p_from = fitz.Point(box_rects[i].x1, y0 + box_h / 2)
        p_to = fitz.Point(box_rects[i + 1].x0, y0 + box_h / 2)
        _draw_arrow(shape, p_from, p_to)

    shape.commit()

    for rect, (ko, en) in zip(box_rects, DIAGRAM_BOXES):
        page.insert_htmlbox(
            fitz.Rect(rect.x0 - 10, rect.y0 + 8, rect.x1 + 10, rect.y1 + 20),
            f'<p style="{LABEL_CSS}"><b>{ko}</b><br/>{en}</p>',
        )

    page.insert_htmlbox(
        fitz.Rect(MARGIN, y0 + box_h + 40, PAGE_W - MARGIN, y0 + box_h + 80),
        f'<p style="{CAPTION_CSS}">{DIAGRAM_CAPTION}</p>',
    )


def build_page3(page: fitz.Page) -> None:
    page.insert_htmlbox(
        fitz.Rect(MARGIN, 60, PAGE_W - MARGIN, 180),
        f'<p style="{BODY_CSS}">{PAGE3_BODY}</p>',
    )

    n_rows = len(TABLE_ROWS)
    n_cols = len(TABLE_ROWS[0])
    table_x0, table_y0 = MARGIN, 220
    table_w = PAGE_W - 2 * MARGIN
    row_h = 32
    col_w = table_w / n_cols
    table_h = row_h * n_rows

    shape = page.new_shape()
    for r in range(n_rows + 1):
        y = table_y0 + r * row_h
        shape.draw_line(fitz.Point(table_x0, y), fitz.Point(table_x0 + table_w, y))
    for c in range(n_cols + 1):
        x = table_x0 + c * col_w
        shape.draw_line(fitz.Point(x, table_y0), fitz.Point(x, table_y0 + table_h))
    shape.finish(color=(0, 0, 0), width=1.0)

    # 헤더 행 음영
    shape.draw_rect(fitz.Rect(table_x0, table_y0, table_x0 + table_w, table_y0 + row_h))
    shape.finish(color=None, fill=(0.9, 0.9, 0.9))
    shape.commit()

    for r, row in enumerate(TABLE_ROWS):
        for c, text in enumerate(row):
            cell = fitz.Rect(
                table_x0 + c * col_w + 4,
                table_y0 + r * row_h + 4,
                table_x0 + (c + 1) * col_w - 4,
                table_y0 + (r + 1) * row_h - 4,
            )
            weight = "font-weight: bold;" if r == 0 else ""
            page.insert_htmlbox(cell, f'<p style="{CELL_CSS}{weight}">{text}</p>')

    page.insert_htmlbox(
        fitz.Rect(table_x0, table_y0 + table_h + 16, PAGE_W - MARGIN, table_y0 + table_h + 50),
        f'<p style="{CAPTION_CSS}">{TABLE_CAPTION}</p>',
    )


def build_vector_doc() -> fitz.Document:
    doc = fitz.open()
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    build_page1(p1)
    p2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    build_page2(p2)
    p3 = doc.new_page(width=PAGE_W, height=PAGE_H)
    build_page3(p3)
    return doc


def rasterize_to_image_only_pdf(vector_doc: fitz.Document, dpi: int) -> fitz.Document:
    """벡터 문서를 dpi로 래스터화해 텍스트 레이어 없는 이미지-only PDF로 재조립."""
    out = fitz.open()
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    for page in vector_doc:
        pix = page.get_pixmap(matrix=matrix)
        img_page = out.new_page(width=page.rect.width, height=page.rect.height)
        img_page.insert_image(img_page.rect, pixmap=pix)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="저작권 프리 데모 샘플 PDF 생성")
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "samples" / "sample.pdf",
        help="출력 경로 (기본: samples/sample.pdf)",
    )
    ap.add_argument("--dpi", type=int, default=RENDER_DPI, help="래스터 DPI (기본: 200)")
    args = ap.parse_args()

    vector_doc = build_vector_doc()
    image_doc = rasterize_to_image_only_pdf(vector_doc, args.dpi)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # garbage=4 + deflate=True: insert_image(pixmap=...)가 압축 없이 저장한 raw
    # 스트림을 정리·압축한다 (미적용 시 3페이지짜리가 수십 MB로 부풀어 커밋 불가).
    image_doc.save(args.out, garbage=4, deflate=True, clean=True)
    vector_doc.close()

    # 검증: 텍스트 레이어가 없어야 스캔본과 동일 조건 (--ocr 경로 발동)
    check = fitz.open(args.out)
    total_text = "".join(p.get_text() for p in check)
    if total_text.strip():
        raise SystemExit(
            f"오류: 생성된 PDF에 텍스트 레이어가 남아있음 ({len(total_text)}자). "
            "이미지-only 재조립이 실패했을 가능성이 있음."
        )
    print(f"완료: {args.out} ({check.page_count}페이지, 텍스트 레이어 없음 확인됨)")
    check.close()
    image_doc.close()


if __name__ == "__main__":
    main()
