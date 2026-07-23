"""이미지 트림 유틸 — 페이지 여백 제거 및 크롭 테두리 정리.

세로 모니터 스크린샷 캡처는 상하 여백이 크고, Mistral OCR은 페이지를 내부
~1020px로 정규화하므로 여백을 미리 잘라내면 콘텐츠 글자가 정규화 후 더
크게 보여 인식률이 좋아진다. 그림 크롭에 페이지 장식 테두리가 물려 나오는
문제도 같은 유틸로 가장자리 균일색을 제거해 해결한다.
"""

import numpy as np
from PIL import Image

_FRAME_WIDTH = 2  # 배경색 판정에 쓰는 바깥 프레임 두께(px)
_DEFAULT_TOL = 12
_DEFAULT_OCCUPANCY = 0.005


def _scan_rgb(img: Image.Image) -> np.ndarray:
    """배경색 판정용 RGB 배열(int16)을 만든다.

    입력에 알파 채널(RGBA/LA/투명도 있는 P)이 있으면 흰 배경에 합성한 뒤
    변환한다 — 투명 영역의 RGB 값은 임의값일 수 있어(예: 완전 투명인데 RGB는
    가비지 컬러) 알파를 무시하고 그대로 스캔하면 투명 여백이 노이즈투성이
    콘텐츠로 오인될 수 있다.
    """
    scan_src = img
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if has_alpha:
        rgba = img.convert("RGBA")
        white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        scan_src = Image.alpha_composite(white_bg, rgba)
    return np.asarray(scan_src.convert("RGB"), dtype=np.int16)


def _uniform_bbox(
    rgb: np.ndarray, tol: int, occupancy: float
) -> tuple[int, int, int, int] | None:
    """바깥 2px 프레임 픽셀의 채널별 중앙값을 배경색으로 삼아, 배경색과의
    채널 최대 편차가 tol 초과인 픽셀 비율이 occupancy를 넘는 가장자리
    행/열로 둘러싸인 콘텐츠 bbox(left, top, right, bottom / inclusive)를
    반환한다. 배경과 구분되는 콘텐츠가 없으면 None.
    """
    frame = np.concatenate([
        rgb[:_FRAME_WIDTH, :, :].reshape(-1, 3),
        rgb[-_FRAME_WIDTH:, :, :].reshape(-1, 3),
        rgb[:, :_FRAME_WIDTH, :].reshape(-1, 3),
        rgb[:, -_FRAME_WIDTH:, :].reshape(-1, 3),
    ])
    bg = np.median(frame, axis=0)

    diff = np.abs(rgb - bg).max(axis=2)
    is_content = diff > tol

    row_occupancy = is_content.mean(axis=1)
    col_occupancy = is_content.mean(axis=0)
    rows = np.where(row_occupancy > occupancy)[0]
    cols = np.where(col_occupancy > occupancy)[0]
    if rows.size == 0 or cols.size == 0:
        return None

    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def trim_uniform_margins(
    img: Image.Image,
    tol: int = _DEFAULT_TOL,
    pad: int = 8,
    min_keep: float = 0.25,
    occupancy: float = _DEFAULT_OCCUPANCY,
) -> Image.Image:
    """이미지 가장자리의 균일한 여백을 제거한다.

    바깥 2px 프레임 픽셀들의 채널별 중앙값을 배경색으로 삼고, 배경색과의
    채널 최대 편차가 tol 초과인 픽셀 비율이 occupancy를 넘는 가장자리
    행/열을 콘텐츠로 판정해 안쪽으로 제거한다. 남은 콘텐츠 둘레에는
    pad px 여백을 유지한다.

    입력에 알파 채널(RGBA/LA/투명도 있는 P)이 있으면 배경색 판정용 스캔은
    흰 배경에 합성한 뒤 수행한다 — 투명 영역의 RGB 값은 임의값일 수 있어
    (예: 완전 투명인데 RGB는 가비지 컬러) 알파를 무시하고 그대로 스캔하면
    투명 여백이 노이즈투성이 콘텐츠로 오인될 수 있다. 실제 크롭은 항상
    원본 이미지(알파 포함)에 대해 수행되어 모드/투명도가 보존된다.

    안전장치: 트림 결과 면적이 원본의 min_keep 미만이면 원본을 그대로
    반환한다 (전면 사진 페이지 등 여백이 없는 이미지의 과잉 트림 방지).

    Args:
        img: 입력 이미지 (모드 무관 — 배경색 판정은 RGB로 변환해 수행)
        tol: 배경으로 간주할 채널 최대 편차 허용치
        pad: 트림 후 콘텐츠 둘레에 남길 여백(px)
        min_keep: 트림 결과가 유지해야 할 최소 면적 비율(원본 대비, 0~1)
        occupancy: 행/열을 콘텐츠로 판정할 tol 초과 픽셀 비율 임계치(0~1).
            이 값 이하 비율의 고립 노이즈(스캔 먼지, JPEG 노이즈 등)는
            배경으로 간주해 무시한다.

    Returns:
        트림된 이미지 (원본 모드 유지). 트림할 여백이 없거나 안전장치가
        발동하면 원본 객체를 그대로 반환한다.
    """
    w, h = img.size
    if w <= 2 * _FRAME_WIDTH or h <= 2 * _FRAME_WIDTH:
        return img

    rgb = _scan_rgb(img)
    bbox = _uniform_bbox(rgb, tol, occupancy)
    if bbox is None:
        return img  # 배경과 구분되는 콘텐츠 없음 — 트림할 것 없음

    left, top, right, bottom = bbox

    top = max(0, top - pad)
    left = max(0, left - pad)
    bottom = min(h - 1, bottom + pad)
    right = min(w - 1, right + pad)

    trimmed_area = (right - left + 1) * (bottom - top + 1)
    if trimmed_area < min_keep * w * h:
        return img  # 과잉 트림 안전장치

    return img.crop((left, top, right + 1, bottom + 1))


def _edge_band_depth(mask: np.ndarray, max_band: int, span_min: float) -> int:
    """mask(행 방향 bool 배열)의 위쪽 가장자리부터 max_band까지, 각 행의
    True 비율이 span_min 이상인 연속 구간의 깊이(px)를 반환한다.

    mask는 이미 "이 변에서 바라본" 방향으로 정렬돼 있어야 한다 — 호출자가
    top/bottom/left/right에 맞게 배열을 뒤집거나 전치해 넘긴다.
    """
    depth = 0
    limit = min(max_band, mask.shape[0])
    for i in range(limit):
        if mask[i, :].mean() >= span_min:
            depth += 1
        else:
            break
    return depth


def strip_chromatic_frame(
    img: Image.Image,
    max_band: int = 8,
    chroma_min: int = 40,
    span_min: float = 0.7,
    max_iter: int = 3,
) -> Image.Image:
    """책 장식용 채도 높은 둥근 프레임 선을 그림 크롭 가장자리에서 제거한다.

    trim_uniform_margins는 배경색과 다른 픽셀이 섞인 행/열을 콘텐츠로 보고
    멈추므로, 4변 전체(또는 대부분)를 가로지르는 장식 프레임 선은 "콘텐츠"로
    오판되어 남는다. 이 함수는 가장자리 근처 max_band px 이내에서 채도
    (max(R,G,B)-min(R,G,B))가 chroma_min 이상인 픽셀이 해당 행/열의
    span_min 비율 이상을 차지하는 연속 밴드를 변마다 찾아 제거한다.

    검은/회색(무채색) 선은 채도가 0에 가까워 chroma_min을 넘지 못하므로
    이 로직으로는 절대 제거되지 않는다 — 표 등 실제 콘텐츠 테두리 보호.

    각 반복은: ① 현재 콘텐츠 bbox를 pad=0 기준으로 재계산(프레임 제거로
    새로 드러난 여백을 다음 반복에서 마저 정리) ② 4변에서 채도 밴드 탐지
    후 bbox를 안쪽으로 좁힌다. 어느 변에서도 밴드가 제거되지 않으면 반복을
    종료한다. bbox 계산은 원본 이미지 좌표계에서 좌표만 갱신할 뿐 실제로
    이미지를 자르지 않는다 — 마지막에 pad=2를 적용할 때 원본의 실제 배경
    픽셀을 그대로 활용하기 위함이다(중간에 pad=0으로 물리적으로 잘라내면
    바깥 여백 픽셀이 사라져 마지막 pad=2가 복원할 배경이 없어진다).

    Args:
        img: 입력 이미지 (모드 무관 — 채도 판정은 RGB 변환 후 수행)
        max_band: 변당 탐지할 최대 밴드 두께(px)
        chroma_min: 프레임 픽셀로 판정할 최소 채도
        span_min: 밴드로 판정할 행/열 내 채도 픽셀 비율 임계치(0~1)
        max_iter: 반복 상한

    Returns:
        프레임이 제거되고 pad=2로 트림된 이미지. 프레임이 없으면
        trim_uniform_margins(img, pad=2)와 동일한 결과.
    """
    w0, h0 = img.size
    # 절대 좌표(원본 img 기준) 콘텐츠 bbox. 처음엔 이미지 전체.
    left, top, right, bottom = 0, 0, w0 - 1, h0 - 1

    for _ in range(max_iter):
        sub = img.crop((left, top, right + 1, bottom + 1))
        w, h = sub.size
        if w <= 2 * _FRAME_WIDTH or h <= 2 * _FRAME_WIDTH:
            break

        # ① 현재 영역 안에서 균일 여백을 pad=0 기준으로 좁힌다
        bbox = _uniform_bbox(_scan_rgb(sub), _DEFAULT_TOL, _DEFAULT_OCCUPANCY)
        if bbox is None:
            break  # 배경과 구분되는 콘텐츠 없음 — 더 좁힐 것 없음
        bl, bt, br, bb = bbox
        left, top = left + bl, top + bt
        right, bottom = left + (br - bl), top + (bb - bt)

        # ② 좁혀진 영역의 4변에서 채도 밴드 탐지
        sub = img.crop((left, top, right + 1, bottom + 1))
        rgb = _scan_rgb(sub)
        chroma = rgb.max(axis=2) - rgb.min(axis=2)
        is_chromatic = chroma >= chroma_min
        h, w = is_chromatic.shape

        d_top = _edge_band_depth(is_chromatic, max_band, span_min)
        d_bottom = _edge_band_depth(is_chromatic[::-1, :], max_band, span_min)
        d_left = _edge_band_depth(is_chromatic.T, max_band, span_min)
        d_right = _edge_band_depth(is_chromatic[:, ::-1].T, max_band, span_min)

        # 상하/좌우 밴드 합이 전체를 잠식하지 않도록 클램프
        if d_top + d_bottom >= h:
            d_top = d_bottom = 0
        if d_left + d_right >= w:
            d_left = d_right = 0

        if d_top == d_bottom == d_left == d_right == 0:
            break  # 아무 변도 안 벗겨짐 — 종료

        top += d_top
        bottom -= d_bottom
        left += d_left
        right -= d_right

    # ③ 마지막 pad=2 — 원본 img에서 콘텐츠 bbox 주변 실제 배경 픽셀을 포함해
    # 다시 크롭한 뒤 trim_uniform_margins로 정리한다(안전장치 min_keep 등 재사용).
    pad = 2
    fl = max(0, left - pad)
    ft = max(0, top - pad)
    fr = min(w0 - 1, right + pad)
    fb = min(h0 - 1, bottom + pad)
    final_sub = img.crop((fl, ft, fr + 1, fb + 1))
    return trim_uniform_margins(final_sub, pad=pad)
