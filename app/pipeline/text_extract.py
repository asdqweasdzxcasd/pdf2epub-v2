"""텍스트 PDF에서 추출한 텍스트로 레이아웃을 구성한다.

OCR/레이아웃 분석을 건너뛰고 다음 두 가지 경로로 페이지를 처리한다:

1. 텍스트 페이지 (또는 텍스트+임베디드 이미지 페이지): 추출된 텍스트를 paragraph 블록으로,
   임베디드 이미지를 figure 블록으로 변환하고 y좌표 기준으로 정렬해 합친다.

2. 이미지만 있는 페이지 (스캔 페이지 등, 텍스트가 거의 없음): 페이지 PNG 자체를
   figure로 임베드한다. PDF 구조상 임베디드 이미지가 잘 안 잡히는 경우의 fallback.
"""

import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.pipeline.layout import Block, BlockType, PageLayout

_BULLETS = {"◼", "◻", "●", "○", "•", "·", "▪", "▫", "▶", "►", "‣", "⁃", "-", "–", "—"}

# 페이지를 "이미지 페이지"로 간주하는 텍스트 길이 임계값 (50자 미만이면 이미지 페이지)
# pdf_render.TEXT_THRESHOLD와 같은 값. 순환 import 피하려고 여기 따로 둠.
_PAGE_IMAGE_THRESHOLD = 50


def build_layouts_from_text(render_result, figures_dir: Path, progress) -> list[PageLayout]:
    figures_dir.mkdir(parents=True, exist_ok=True)
    page_layouts: list[PageLayout] = []
    page_count = render_result.page_count

    for i in range(page_count):
        blocks: list[Block] = []

        # 페이지 텍스트 길이 측정 — 너무 적으면 "이미지 페이지"로 간주하고 PNG 임베드
        page_text = (
            render_result.page_texts[i]
            if i < len(render_result.page_texts)
            else ""
        )
        if len(page_text.strip()) < _PAGE_IMAGE_THRESHOLD:
            page_block = embed_page_as_figure(render_result, i, figures_dir)
            if page_block is not None:
                page_layouts.append(PageLayout(page_num=i, blocks=[page_block]))
                if (i + 1) % 10 == 0 or i == page_count - 1:
                    pct = 20 + int((i + 1) / page_count * 65)
                    progress.update(pct, "extract", f"페이지 {i + 1}/{page_count} (이미지)")
                continue
            # PNG 임베드 실패 시 아래 일반 경로로 fallback (빈 페이지여도 일관성 유지)

        raw_text_blocks = (
            render_result.page_text_blocks[i]
            if i < len(render_result.page_text_blocks)
            else []
        )

        # 페이지 번호 필터링 + 불릿 합치기
        text_blocks: list[tuple[float, float, float, float, str]] = []
        for tb in raw_text_blocks:
            x0, y0, x1, y1, block_text = tb
            raw_lines = [
                line for line in block_text.split("\n")
                if line.strip() and not line.strip().isdigit()
            ]
            merged_lines: list[str] = []
            for line in raw_lines:
                if merged_lines and merged_lines[-1].strip() in _BULLETS:
                    merged_lines[-1] = merged_lines[-1].strip() + " " + line.strip()
                else:
                    merged_lines.append(line)
            cleaned = "\n".join(merged_lines)
            if cleaned.strip():
                text_blocks.append((x0, y0, x1, y1, cleaned))

        page_images = (
            render_result.embedded_images[i]
            if i < len(render_result.embedded_images)
            else []
        )

        figure_blocks: list[Block] = []
        for img_idx, (xref, rect, image_bytes) in enumerate(page_images):
            filename = f"page_{i:04d}_img_{img_idx + 1:03d}.png"
            save_path = figures_dir / filename
            try:
                img = Image.open(BytesIO(image_bytes))
                if img.mode in ("CMYK", "P"):
                    img = img.convert("RGB")
                img.save(str(save_path), format="PNG")
                img.close()
            except Exception:
                continue

            figure_blocks.append(Block(
                block_type=BlockType.FIGURE,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                confidence=1.0,
                image_path=filename,
            ))

        elements: list[tuple[float, str, object]] = []
        for tb in text_blocks:
            x0, y0, x1, y1, cleaned = tb
            block = Block(
                block_type=BlockType.PARAGRAPH,
                bbox=(x0, y0, x1, y1),
                confidence=1.0,
                text=cleaned,
            )
            elements.append((y0, "text", block))

        for fb in figure_blocks:
            elements.append((fb.bbox[1], "figure", fb))

        elements.sort(key=lambda e: e[0])

        for _, _, elem in elements:
            blocks.append(elem)  # type: ignore[arg-type]

        page_layouts.append(PageLayout(page_num=i, blocks=blocks))

        if (i + 1) % 10 == 0 or i == page_count - 1:
            pct = 20 + int((i + 1) / page_count * 65)
            progress.update(pct, "text_extract", f"텍스트 추출 {i + 1}/{page_count}")

    return page_layouts


def embed_page_as_figure(render_result, page_num: int, figures_dir: Path) -> Block | None:
    """이미지 페이지: render()가 미리 만든 페이지 PNG를 figures_dir로 복사하고
    figure Block으로 반환한다. 페이지 PNG 자체가 없으면 None.
    """
    page_images = getattr(render_result, "page_images", []) or []
    if page_num >= len(page_images):
        return None
    src_path = page_images[page_num]
    if src_path is None or not Path(src_path).exists():
        return None

    filename = f"page_{page_num:04d}_full.png"
    dst_path = figures_dir / filename
    try:
        shutil.copy(str(src_path), str(dst_path))
    except Exception:
        return None

    try:
        with Image.open(dst_path) as img:
            w, h = img.size
    except Exception:
        w, h = 0, 0

    return Block(
        block_type=BlockType.FIGURE,
        bbox=(0.0, 0.0, float(w), float(h)),
        confidence=1.0,
        image_path=filename,
    )


_embed_page_as_figure = embed_page_as_figure  # 하위호환
