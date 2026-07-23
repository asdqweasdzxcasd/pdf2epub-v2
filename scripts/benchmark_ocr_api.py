"""M1 검증: 외부 OCR API 한국어 스캔본 품질 벤치마크.

스캔본 PDF의 지정 페이지를 Mistral OCR API로 보내고, 결과를 사람이 판단할 수 있는
HTML 리포트로 만든다. 텍스트 정확도와 이미지 블록 bbox 품질(다이어그램 분리)이
판단 대상이다. V2 로드맵 M1 단계 전용 — 제품 코드가 아니라 측정 하네스.

사용:
    export MISTRAL_API_KEY=...
    python scripts/benchmark_ocr_api.py 스캔본.pdf --pages 10-25 --out bench_out

출력 (--out 디렉토리):
    response.json          # API 원본 응답
    report.html            # 페이지별 원본/오버레이/추출 텍스트 비교 리포트
    page_NNN.png           # 원본 렌더
    page_NNN_overlay.png   # 블록 bbox 오버레이 (이미지 블록 = 빨강)
    page_NNN.md            # 추출된 마크다운
"""

import argparse
import base64
import html
import json
import os
import sys
from collections import Counter
from pathlib import Path

import cv2
import fitz  # PyMuPDF
import requests

OCR_ENDPOINT = "https://api.mistral.ai/v1/ocr"
RENDER_DPI = 150

# 블록 타입별 오버레이 색 (BGR). 이미지/캡션은 눈에 띄게.
BLOCK_COLORS = {
    "image": (0, 0, 255),      # 빨강 — 다이어그램 분리 품질이 M1의 핵심
    "caption": (0, 128, 255),
    "table": (255, 0, 255),
    "title": (255, 128, 0),
    "text": (0, 200, 0),
}
DEFAULT_COLOR = (160, 160, 160)


def parse_pages(spec: str, page_count: int) -> list[int]:
    """'10-25' 또는 '1,3,5-8' 형식을 0-base 페이지 번호 리스트로 변환한다."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start) - 1, int(end)))
        else:
            pages.append(int(part) - 1)
    valid = sorted({p for p in pages if 0 <= p < page_count})
    if not valid:
        sys.exit(f"페이지 범위가 잘못됨: {spec} (문서 페이지 수: {page_count})")
    return valid


def extract_subpdf(src: fitz.Document, pages: list[int]) -> bytes:
    """지정 페이지만 담은 서브 PDF 바이트를 만든다 (API 호출 비용 절약)."""
    sub = fitz.open()
    for p in pages:
        sub.insert_pdf(src, from_page=p, to_page=p)
    data = sub.tobytes()
    sub.close()
    return data


def call_ocr(pdf_bytes: bytes, model: str, api_key: str) -> dict:
    b64 = base64.b64encode(pdf_bytes).decode()
    payload = {
        "model": model,
        "document": {
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{b64}",
        },
        "include_image_base64": True,
        "include_blocks": True,
        "table_format": "markdown",
    }
    resp = requests.post(
        OCR_ENDPOINT,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=600,
    )
    if resp.status_code != 200:
        sys.exit(f"API 오류 {resp.status_code}: {resp.text[:2000]}")
    return resp.json()


def scale_bbox(block: dict, img_w: int, img_h: int, page_dim: dict) -> tuple[int, int, int, int]:
    """블록 좌표를 렌더 이미지 픽셀 좌표로 변환.

    응답 좌표가 0~1 정규화인지, 페이지 dimensions 기준 픽셀인지 문서상 불명확해서
    둘 다 처리한다: 최대값이 1.5 이하이면 정규화로 간주.
    """
    x0, y0 = block["top_left_x"], block["top_left_y"]
    x1, y1 = block["bottom_right_x"], block["bottom_right_y"]
    if max(x1, y1) <= 1.5:
        return int(x0 * img_w), int(y0 * img_h), int(x1 * img_w), int(y1 * img_h)
    ref_w = page_dim.get("width") or img_w
    ref_h = page_dim.get("height") or img_h
    sx, sy = img_w / ref_w, img_h / ref_h
    return int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)


def draw_overlay(page_png: Path, blocks: list[dict], page_dim: dict, out_path: Path) -> None:
    img = cv2.imread(str(page_png))
    if img is None:
        return
    h, w = img.shape[:2]
    for blk in blocks:
        btype = blk.get("type", "?")
        try:
            x0, y0, x1, y1 = scale_bbox(blk, w, h, page_dim)
        except (KeyError, TypeError):
            continue
        color = BLOCK_COLORS.get(btype, DEFAULT_COLOR)
        thickness = 3 if btype == "image" else 1
        cv2.rectangle(img, (x0, y0), (x1, y1), color, thickness)
        cv2.putText(img, btype, (x0 + 2, max(y0 - 4, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    cv2.imwrite(str(out_path), img)


def build_report(out_dir: Path, pages_meta: list[dict], model: str, usage: dict) -> None:
    type_counts: Counter = Counter()
    for m in pages_meta:
        type_counts.update(m["type_counts"])
    rows = []
    for m in pages_meta:
        counts = ", ".join(f"{t}:{c}" for t, c in sorted(m["type_counts"].items())) or "블록 없음"
        rows.append(f"""
<section>
  <h2>원본 p.{m['src_page']} (요청 {m['index'] + 1}번째)</h2>
  <p>블록: {counts} | 신뢰도: {html.escape(str(m['confidence']))}</p>
  <div class="row">
    <figure><figcaption>원본</figcaption><img src="{m['png']}"></figure>
    <figure><figcaption>블록 오버레이 (빨강=image)</figcaption><img src="{m['overlay']}"></figure>
    <figure class="md"><figcaption>추출 텍스트</figcaption><pre>{html.escape(m['markdown'])}</pre></figure>
  </div>
</section>""")
    summary = ", ".join(f"{t}: {c}" for t, c in type_counts.most_common())
    doc = f"""<!doctype html><meta charset="utf-8">
<title>M1 OCR 벤치마크 — {html.escape(model)}</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
.row {{ display: flex; gap: 1rem; align-items: flex-start; }}
figure {{ flex: 1; margin: 0; min-width: 0; }}
img {{ max-width: 100%; border: 1px solid #ccc; }}
pre {{ white-space: pre-wrap; font-size: 12px; background: #f6f6f6;
      padding: .5rem; max-height: 80vh; overflow-y: auto; }}
section {{ border-top: 2px solid #333; margin-top: 2rem; }}
</style>
<h1>M1 OCR 벤치마크 — {html.escape(model)}</h1>
<p>전체 블록 분포: {summary}</p>
<p>usage: {html.escape(json.dumps(usage, ensure_ascii=False))}</p>
<p>판단 기준: ① 한국어 텍스트 오인식률 ② image 블록(빨강)이 다이어그램을 온전히 감싸는가
③ 읽기 순서가 자연스러운가</p>
{''.join(rows)}"""
    (out_dir / "report.html").write_text(doc, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mistral OCR 한국어 스캔본 벤치마크 (M1)")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", default="1-10", help="예: 10-25 또는 1,3,5-8 (1-base)")
    ap.add_argument("--model", default="mistral-ocr-latest")
    ap.add_argument("--out", type=Path, default=Path("bench_out"))
    args = ap.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        sys.exit("MISTRAL_API_KEY 환경변수를 설정하세요 (https://console.mistral.ai)")
    if not args.pdf.exists():
        sys.exit(f"파일 없음: {args.pdf}")

    src = fitz.open(args.pdf)
    pages = parse_pages(args.pages, src.page_count)
    est = len(pages) * 0.004
    print(f"대상: {args.pdf.name} — {len(pages)}페이지, 예상 비용 ~${est:.3f}")

    args.out.mkdir(parents=True, exist_ok=True)
    zoom = RENDER_DPI / 72
    for i, p in enumerate(pages):
        pix = src[p].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        pix.save(args.out / f"page_{i:03d}.png")

    print(f"{args.model} 호출 중...")
    result = call_ocr(extract_subpdf(src, pages), args.model, api_key)
    (args.out / "response.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    pages_meta = []
    for page in result.get("pages", []):
        i = page.get("index", 0)
        blocks = page.get("blocks") or []
        markdown = page.get("markdown", "")
        (args.out / f"page_{i:03d}.md").write_text(markdown, encoding="utf-8")
        draw_overlay(
            args.out / f"page_{i:03d}.png",
            blocks,
            page.get("dimensions") or {},
            args.out / f"page_{i:03d}_overlay.png",
        )
        pages_meta.append({
            "index": i,
            "src_page": pages[i] + 1 if i < len(pages) else "?",
            "png": f"page_{i:03d}.png",
            "overlay": f"page_{i:03d}_overlay.png",
            "markdown": markdown,
            "type_counts": Counter(b.get("type", "?") for b in blocks),
            "confidence": page.get("confidence_scores", {}),
        })

    build_report(args.out, pages_meta, args.model, result.get("usage_info", {}))
    src.close()
    print(f"완료 → {args.out / 'report.html'} 를 브라우저로 여세요")


if __name__ == "__main__":
    main()
