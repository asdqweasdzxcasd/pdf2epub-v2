"""이미지 트림 유틸 — 페이지 여백 제거 및 크롭 테두리 정리.

세로 모니터 스크린샷 캡처는 상하 여백이 크고, Mistral OCR은 페이지를 내부
~1020px로 정규화하므로 여백을 미리 잘라내면 콘텐츠 글자가 정규화 후 더
크게 보여 인식률이 좋아진다. 그림 크롭에 페이지 장식 테두리가 물려 나오는
문제도 같은 유틸로 가장자리 균일색을 제거해 해결한다.
"""

import numpy as np
from PIL import Image

_FRAME_WIDTH = 2  # 배경색 판정에 쓰는 바깥 프레임 두께(px)


def trim_uniform_margins(
    img: Image.Image,
    tol: int = 12,
    pad: int = 8,
    min_keep: float = 0.25,
) -> Image.Image:
    """이미지 가장자리의 균일한 여백을 제거한다.

    바깥 2px 프레임 픽셀들의 채널별 중앙값을 배경색으로 삼고, 배경색과의
    채널 최대 편차가 tol 이하인 가장자리 행/열을 안쪽으로 제거한다.
    남은 콘텐츠 둘레에는 pad px 여백을 유지한다.

    안전장치: 트림 결과 면적이 원본의 min_keep 미만이면 원본을 그대로
    반환한다 (전면 사진 페이지 등 여백이 없는 이미지의 과잉 트림 방지).

    Args:
        img: 입력 이미지 (모드 무관 — 배경색 판정은 RGB로 변환해 수행)
        tol: 배경으로 간주할 채널 최대 편차 허용치
        pad: 트림 후 콘텐츠 둘레에 남길 여백(px)
        min_keep: 트림 결과가 유지해야 할 최소 면적 비율(원본 대비, 0~1)

    Returns:
        트림된 이미지 (원본 모드 유지). 트림할 여백이 없거나 안전장치가
        발동하면 원본 객체를 그대로 반환한다.
    """
    w, h = img.size
    if w <= 2 * _FRAME_WIDTH or h <= 2 * _FRAME_WIDTH:
        return img

    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)

    frame = np.concatenate([
        rgb[:_FRAME_WIDTH, :, :].reshape(-1, 3),
        rgb[-_FRAME_WIDTH:, :, :].reshape(-1, 3),
        rgb[:, :_FRAME_WIDTH, :].reshape(-1, 3),
        rgb[:, -_FRAME_WIDTH:, :].reshape(-1, 3),
    ])
    bg = np.median(frame, axis=0)

    diff = np.abs(rgb - bg).max(axis=2)
    is_content = diff > tol

    rows = np.where(is_content.any(axis=1))[0]
    cols = np.where(is_content.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return img  # 배경과 구분되는 콘텐츠 없음 — 트림할 것 없음

    top, bottom = int(rows[0]), int(rows[-1])
    left, right = int(cols[0]), int(cols[-1])

    top = max(0, top - pad)
    left = max(0, left - pad)
    bottom = min(h - 1, bottom + pad)
    right = min(w - 1, right + pad)

    trimmed_area = (right - left + 1) * (bottom - top + 1)
    if trimmed_area < min_keep * w * h:
        return img  # 과잉 트림 안전장치

    return img.crop((left, top, right + 1, bottom + 1))
