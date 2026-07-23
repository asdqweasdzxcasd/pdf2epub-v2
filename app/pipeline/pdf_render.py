"""PyMuPDF 기반 PDF 페이지 렌더링 및 텍스트 추출"""

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class RenderResult:
    """PDF 렌더링 결과"""

    page_count: int
    ocr_needed: bool
    page_images: list[Path]  # 렌더링된 페이지 이미지 경로들
    page_texts: list[str]  # 추출된 텍스트 (텍스트 PDF일 경우)
    # 페이지별 텍스트 블록 + 좌표 [(x0, y0, x1, y1, text), ...]
    page_text_blocks: list[list[tuple[float, float, float, float, str]]]
    # 각 페이지의 임베디드 이미지 목록 (텍스트 PDF에서 인라인 이미지 추출용)
    # [(xref, rect, image_bytes), ...]
    embedded_images: list[list[tuple[int, fitz.Rect, bytes]]]


# 페이지당 평균 텍스트 길이 임계값 (7.3: 초기 50자)
TEXT_THRESHOLD = 50

# 렌더링 DPI (7.1: DPI 300)
RENDER_DPI = 300


def is_image_pdf(pdf_path: Path) -> bool:
    """PDF가 이미지 PDF (텍스트 layer 없음)인지 빠르게 판단한다.

    렌더링 없이 텍스트 길이만 측정하므로 가벼움. API 단에서 업로드 직후
    사전 거부용으로 사용.

    판단 기준은 render() 와 동일: 페이지당 평균 텍스트 길이 < TEXT_THRESHOLD.

    Args:
        pdf_path: PDF 파일 경로

    Returns:
        True = 이미지 PDF (OCR 필요), False = 텍스트 PDF
    """
    doc = fitz.open(str(pdf_path))
    try:
        if len(doc) == 0:
            return True  # 빈 PDF는 처리 불가능 → 거부
        total_text_len = sum(len(page.get_text().strip()) for page in doc)
        avg_per_page = total_text_len / len(doc)
        return avg_per_page < TEXT_THRESHOLD
    finally:
        doc.close()


def render(pdf_path: Path, temp_dir: Path, progress_cb=None) -> RenderResult:
    """PDF를 페이지별 이미지로 렌더링하고, 텍스트 PDF 여부를 판별한다.

    7.2 명세:
    - 입력: uploads PDF
    - 출력: pages PNG, page_count, ocr_needed 플래그

    7.3 텍스트 PDF 분기:
    - 페이지당 평균 텍스트 길이 >= TEXT_THRESHOLD 이면 ocr_needed=False
    - 텍스트 블록 + 페이지 내부 이미지도 함께 추출

    Args:
        pdf_path: 입력 PDF 경로
        temp_dir: 임시 파일 디렉토리 (temp/{job_id}/)
        progress_cb: 진행률 콜백 (ProgressCallback)

    Returns:
        RenderResult

    Raises:
        fitz.FileDataError: PDF 파싱 실패 시 (7.2: 잡 전체 INVALID_PDF failed)
    """
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)

    pages_dir = temp_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    page_images: list[Path] = []
    page_texts: list[str] = []
    page_text_blocks: list[list[tuple[float, float, float, float, str]]] = []
    embedded_images: list[list[tuple[int, fitz.Rect, bytes]]] = []
    total_text_len = 0

    # DPI 300 렌더링용 변환 행렬 (72dpi 기준 스케일링)
    mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)

    for i, page in enumerate(doc):
        # 텍스트 추출
        text = page.get_text()
        page_texts.append(text)
        total_text_len += len(text.strip())

        # 페이지를 PNG로 렌더링
        pix = page.get_pixmap(matrix=mat)
        img_path = pages_dir / f"{i:04d}.png"
        pix.save(str(img_path))
        page_images.append(img_path)

        # 텍스트 블록 + 좌표 추출 (텍스트 PDF 분기에서 위치 기반 정렬용)
        blocks = page.get_text("blocks")
        text_blocks = []
        for b in blocks:
            # block type 0 = text, 1 = image
            if b[6] == 0:
                block_text = b[4].strip()
                if block_text:
                    text_blocks.append((b[0], b[1], b[2], b[3], block_text))
        page_text_blocks.append(text_blocks)

        # 페이지 내 임베디드 이미지 추출 (텍스트 PDF 분기에서 사용)
        page_embedded = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                img_rects = page.get_image_rects(xref)
                rect = img_rects[0] if img_rects else fitz.Rect(0, 0, 0, 0)
                base_image = doc.extract_image(xref)
                if base_image:
                    page_embedded.append((xref, rect, base_image["image"]))
            except Exception:
                # 개별 이미지 추출 실패는 무시하고 계속 진행
                continue
        embedded_images.append(page_embedded)

        # 진행률 보고 (render 단계: 0~10%)
        if progress_cb:
            pct = int((i + 1) / page_count * 10)
            progress_cb.update(pct, "render", f"페이지 {i + 1}/{page_count}")

    doc.close()

    # 텍스트 PDF 판정 (7.3: 페이지당 평균 텍스트 길이가 임계값 이상이면 OCR 불필요)
    avg_text_len = total_text_len / page_count if page_count > 0 else 0
    ocr_needed = avg_text_len < TEXT_THRESHOLD

    return RenderResult(
        page_count=page_count,
        ocr_needed=ocr_needed,
        page_images=page_images,
        page_texts=page_texts,
        page_text_blocks=page_text_blocks,
        embedded_images=embedded_images,
    )
