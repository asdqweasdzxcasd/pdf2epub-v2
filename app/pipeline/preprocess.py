"""OpenCV 기반 이미지 전처리 (deskew, 이진화, 노이즈 제거)"""

import logging
import shutil
from pathlib import Path

import cv2
import numpy as np

from app.pipeline.progress import ProgressCallback

logger = logging.getLogger(__name__)


def preprocess_pages(
    pages_dir: Path,
    clean_dir: Path,
    progress_cb: ProgressCallback | None = None,
    page_count: int = 0,
) -> list[Path]:
    """렌더링된 페이지 이미지들을 전처리한다.

    Args:
        pages_dir: 원본 이미지 디렉토리 (temp/{job_id}/pages/)
        clean_dir: 전처리 결과 디렉토리 (temp/{job_id}/clean/)
        progress_cb: 진행률 콜백 (ProgressCallback 프로토콜)
        page_count: 총 페이지 수 (0이면 파일 개수로 자동 결정)

    Returns:
        전처리된 이미지 경로 리스트 (정렬됨)
    """
    clean_dir.mkdir(parents=True, exist_ok=True)

    page_files = sorted(pages_dir.glob("*.png"))
    if not page_files:
        logger.warning("전처리할 페이지 이미지가 없음: %s", pages_dir)
        return []

    if not page_count:
        page_count = len(page_files)

    clean_paths: list[Path] = []

    for i, page_path in enumerate(page_files):
        out_path = clean_dir / page_path.name
        try:
            img = cv2.imread(str(page_path), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise ValueError(f"이미지 로드 실패: {page_path}")

            cleaned = _clean_page(img)
            cv2.imwrite(str(out_path), cleaned)

        except Exception as e:
            logger.warning("전처리 실패, 원본 사용: %s - %s", page_path.name, e)
            # 단일 페이지 실패 시 원본으로 폴백, 잡은 계속
            shutil.copy2(str(page_path), str(out_path))

        clean_paths.append(out_path)

        # 진행률 보고 (preprocess 단계: 10~20%)
        if progress_cb:
            pct = 10 + int((i + 1) / page_count * 10)
            progress_cb.update(pct, "preprocess", f"전처리 {i + 1}/{page_count}")

    return clean_paths


def _clean_page(img: np.ndarray) -> np.ndarray:
    """단일 페이지에 전처리 파이프라인을 적용한다.

    순서: 노이즈 제거 → 기울기 보정 → 적응형 이진화

    Args:
        img: 그레이스케일 이미지

    Returns:
        전처리된 이미지
    """
    # 1. 노이즈 제거 (가우시안 블러)
    denoised = cv2.GaussianBlur(img, (3, 3), 0)

    # 2. 기울기 보정 (deskew)
    corrected = _deskew(denoised)

    # 3. 적응형 이진화 (Otsu)
    _, binary = cv2.threshold(
        corrected, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    return binary


def _deskew(img: np.ndarray, max_angle: float = 5.0) -> np.ndarray:
    """이미지 기울기를 보정한다.

    minAreaRect로 텍스트 영역의 회전 각도를 추정하고,
    허용 범위 내일 때만 보정을 적용한다.

    Args:
        img: 그레이스케일 이미지
        max_angle: 보정할 최대 각도 (도). 초과 시 보정 생략

    Returns:
        보정된 이미지 (각도가 범위 밖이면 원본 그대로)
    """
    # 어두운 픽셀(텍스트) 좌표 추출
    coords = np.column_stack(np.where(img < 128))
    if len(coords) < 100:
        return img

    # 최소 면적 회전 사각형으로 각도 계산
    angle = cv2.minAreaRect(coords)[-1]

    # OpenCV minAreaRect 각도 범위 보정 (-90, 0]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # 보정 불필요한 경우: 각도가 너무 크거나 무시할 수준
    if abs(angle) > max_angle or abs(angle) < 0.1:
        return img

    # 이미지 중심 기준 회전
    h, w = img.shape
    center = (w // 2, h // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        img,
        rotation_matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )

    return rotated
