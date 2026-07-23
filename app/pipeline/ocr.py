"""Surya OCR 기반 텍스트 추출"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.pipeline.progress import ProgressCallback

logger = logging.getLogger(__name__)


@dataclass
class OcrLine:
    """OCR로 추출된 텍스트 라인"""

    text: str
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    confidence: float


@dataclass
class OcrPageResult:
    """페이지별 OCR 결과"""

    page_num: int
    lines: list[OcrLine]


class SuryaOcrEngine:
    """Surya OCR 엔진. 모델을 한 번 로드하고 재사용한다."""

    def __init__(self, device: str = "cpu"):
        # Surya 환경변수 설정 (import 전에 해야 적용됨)
        os.environ.setdefault("TORCH_DEVICE", device)
        os.environ.setdefault(
            "DETECTOR_BATCH_SIZE", "8" if device == "cpu" else "32"
        )
        os.environ.setdefault(
            "RECOGNITION_BATCH_SIZE", "4" if device == "cpu" else "256"
        )

        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor

        logger.info("Surya OCR 모델 로딩 중...")
        self._det_predictor = DetectionPredictor()
        foundation = FoundationPredictor()
        self._rec_predictor = RecognitionPredictor(foundation)
        logger.info("Surya OCR 모델 로딩 완료")

    def run_ocr(
        self,
        images: list[Image.Image],
    ) -> list[list[OcrLine]]:
        """이미지 리스트에 대해 OCR을 실행한다.

        Surya 0.17+에서는 언어 자동 감지. langs 파라미터 없음.

        Args:
            images: PIL Image 리스트

        Returns:
            페이지별 OcrLine 리스트
        """
        results = self._rec_predictor(
            images, det_predictor=self._det_predictor
        )

        all_lines: list[list[OcrLine]] = []
        for page_result in results:
            page_lines = []
            for line in page_result.text_lines:
                # polygon → bbox 변환 (polygon: [[x,y], ...])
                poly = line.polygon
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                bbox = (min(xs), min(ys), max(xs), max(ys))
                page_lines.append(
                    OcrLine(
                        text=line.text,
                        bbox=bbox,
                        confidence=line.confidence or 0.0,
                    )
                )
            all_lines.append(page_lines)

        return all_lines


def run_ocr_on_pages(
    clean_dir: Path,
    ocr_dir: Path,
    device: str = "cpu",
    progress_cb: ProgressCallback | None = None,
    page_count: int = 0,
) -> list[OcrPageResult]:
    """전처리된 페이지 이미지들에 OCR을 실행한다.

    Args:
        clean_dir: 전처리된 이미지 디렉토리 (temp/{job_id}/clean/)
        ocr_dir: OCR 결과 JSON 저장 디렉토리 (temp/{job_id}/ocr/)
        device: "cpu" 또는 "cuda"
        progress_cb: 진행률 콜백 (ProgressCallback 프로토콜)
        page_count: 총 페이지 수 (0이면 파일 개수로 자동 결정)

    Returns:
        페이지별 OCR 결과 리스트
    """
    ocr_dir.mkdir(parents=True, exist_ok=True)

    page_files = sorted(clean_dir.glob("*.png"))
    if not page_files:
        logger.warning("OCR 처리할 페이지 이미지가 없음: %s", clean_dir)
        return []

    if not page_count:
        page_count = len(page_files)

    if progress_cb:
        progress_cb.update(20, "ocr", "Surya OCR 모델 로딩 중")

    engine = SuryaOcrEngine(device=device)

    # 배치 처리 (메모리 절약을 위해 4페이지씩)
    batch_size = 4
    all_results: list[OcrPageResult] = []

    for batch_start in range(0, len(page_files), batch_size):
        batch_files = page_files[batch_start : batch_start + batch_size]
        batch_images: list[Image.Image] = []

        # 배치 시작 시점에 진행 메시지 갱신 (UI 멈춤 방지)
        # batch 처리에 수십~수백초 걸려서 끝날 때만 갱신하면 화면이 멈춰 보임
        if progress_cb:
            end = min(batch_start + batch_size, page_count)
            pct = 20 + int(batch_start / page_count * 55)
            progress_cb.update(
                pct, "ocr", f"OCR 처리 중 ({batch_start + 1}~{end}/{page_count})"
            )

        for f in batch_files:
            try:
                img = Image.open(f).convert("RGB")
                batch_images.append(img)
            except Exception as e:
                logger.warning("이미지 로드 실패: %s - %s", f.name, e)
                # 빈 이미지로 대체하여 인덱스 정합성 유지
                batch_images.append(Image.new("RGB", (100, 100), "white"))

        try:
            batch_lines = engine.run_ocr(batch_images)
        except Exception as e:
            logger.error("OCR 배치 실패 (page %d~%d): %s", batch_start, batch_start + len(batch_files) - 1, e)
            batch_lines = [[] for _ in batch_images]

        for j, (page_file, lines) in enumerate(zip(batch_files, batch_lines)):
            page_num = batch_start + j
            result = OcrPageResult(page_num=page_num, lines=lines)
            all_results.append(result)

            # 결과를 JSON으로 저장 (디버깅·재처리 용도)
            json_path = ocr_dir / f"{page_file.stem}.json"
            json_data = {
                "page_num": page_num,
                "lines": [
                    {
                        "text": line.text,
                        "bbox": list(line.bbox),
                        "confidence": line.confidence,
                    }
                    for line in lines
                ],
            }
            json_path.write_text(
                json.dumps(json_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 진행률 보고 (ocr 단계: 20~75%)
            if progress_cb:
                pct = 20 + int((page_num + 1) / page_count * 55)
                progress_cb.update(pct, "ocr", f"OCR {page_num + 1}/{page_count}")

        # 배치 이미지 메모리 해제
        for img in batch_images:
            img.close()

    return all_results
