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
_DEFAULT_BLANK_TOL = 8


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


def is_blank_page(img: Image.Image, tol: int = _DEFAULT_BLANK_TOL) -> bool:
    """페이지 이미지가 거의 단색(백지/챕터 구분용 색지 등)인지 판정한다.

    챕터 구분용 백지 페이지는 OCR 블록 0개로 응답되기 쉬운데, 그 페이지
    이미지를 그대로 EPUB에 임베드하면 무의미한 빈 이미지가 남는다 — 이를
    걸러내기 위한 판정 헬퍼.

    _scan_rgb로 얻은 RGB 배열의 채널별 표준편차 중 최댓값이 tol 미만이면
    "거의 단색"으로 판정한다. 그림이 있는 페이지(사진 전면 페이지 등)는
    색 분산이 커서 표준편차가 tol을 훌쩍 넘으므로 영향받지 않는다.

    Args:
        img: 판정할 페이지 이미지 (모드 무관 — RGB로 변환해 스캔)
        tol: 단색으로 볼 채널 표준편차 상한

    Returns:
        거의 단색이면 True.
    """
    rgb = _scan_rgb(img).astype(np.float64)
    std = float(rgb.reshape(-1, 3).std(axis=0).max())
    return std < tol


def ink_coverage(img: Image.Image, threshold: int = 235) -> float:
    """페이지 전체에서 비백색 픽셀이 차지하는 비율을 반환한다.

    표지 판정에 쓰인다 — 디자인된 표지는 배경 이미지/컬러 블록으로 페이지
    대부분을 채우는 반면, 본문 페이지는 흰 여백이 대부분이라 비백색 비율이
    낮다(실측: 표지 0.947, 본문 0.016~0.138).

    픽셀의 min(R,G,B)가 threshold 미만이면 "잉크(비백색)"로 센다 — 순백
    (255,255,255)뿐 아니라 옅은 회색/컬러 배경도 threshold 이하면 여백으로
    보지 않는다.

    Args:
        img: 판정할 페이지 이미지 (모드 무관 — RGB로 변환해 스캔)
        threshold: 비백색으로 볼 min(R,G,B) 상한

    Returns:
        비백색 픽셀 비율 (0~1)
    """
    rgb = _scan_rgb(img)
    non_white = rgb.min(axis=2) < threshold
    return float(non_white.mean())


def chroma_coverage(img: Image.Image, chroma_min: int = 25) -> float:
    """페이지 전체에서 유채색(채도 chroma_min 이상) 픽셀이 차지하는 비율을 반환한다.

    앞부분 디자인 페이지(차례, 책소개 등) 판정에 쓰인다 -- 컬러 챕터 밴드로
    조판된 디자인 페이지는 유채색 픽셀 비율이 높은 반면, 본문 텍스트
    페이지는 검정/회색 글자가 대부분이라 유채색 비율이 매우 낮다(실측:
    디자인 페이지 0.048~0.098, 본문 텍스트 페이지 0.0006~0.0035). 단, 그림이
    있는 본문 페이지는 이 값만으로 0.049까지 올라갈 수 있어 이 헬퍼 단독으로는
    본문/디자인 페이지를 완전히 구분하지 못한다 -- 호출부가 "장 구분 페이지
    이전"이라는 위치 조건과 함께 써야 한다.

    픽셀의 채도(max(R,G,B) - min(R,G,B))가 chroma_min 이상이면 "유채색"으로
    센다.

    Args:
        img: 판정할 페이지 이미지 (모드 무관 -- RGB로 변환해 스캔)
        chroma_min: 유채색으로 볼 최소 채도

    Returns:
        유채색 픽셀 비율 (0~1)
    """
    rgb = _scan_rgb(img)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    return float((chroma >= chroma_min).mean())


_BG_BRIGHT_MIN = 180  # "밝은 픽셀"로 볼 min(R,G,B) 하한
_BG_BRIGHT_OCCUPANCY = 0.3  # 밝은 픽셀이 이 비율 미만이면 배경 추출 포기(그림/표 등)
_BG_WHITE_MIN = 244  # 중앙값 min이 이 값 초과면 흰색으로 보고 None
_BG_DARK_MAX = 180  # 중앙값 min이 이 값 미만이면 너무 어두운 것으로 보고 None


def sample_block_background(
    img: Image.Image, box: tuple[float, float, float, float]
) -> tuple[int, int, int] | None:
    """블록 bbox 영역의 배경색을 페이지 이미지에서 추출한다.

    bbox 영역 픽셀 중 min(R,G,B) > 180인 "밝은 픽셀"(글자/그림 등 어두운
    전경을 제외한 배경 후보)들의 채널별 중앙값을 배경색으로 삼는다. 밝은
    픽셀이 영역의 30% 미만이면 배경을 추출할 수 없는 것으로 보고 None을
    반환한다(사진/그림 영역 등 오탐 방지).

    흰 배경(중앙값 min > 244)은 강조색이 아니므로 None. 너무 어두운
    결과(중앙값 min < 180)도 사진 위 텍스트 등의 오탐일 수 있어 None으로
    처리한다.

    Args:
        img: 페이지 이미지 (모드 무관 — RGB로 변환해 스캔)
        box: 블록 bbox, 픽셀 좌표 (x0, y0, x1, y1). x1/y1은 crop 관례상
            배타적 상한으로 취급한다.

    Returns:
        배경색 (R, G, B) 또는 None
    """
    w, h = img.size
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return None

    region = np.asarray(img.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.int16)
    pixels = region.reshape(-1, 3)

    bright_mask = pixels.min(axis=1) > _BG_BRIGHT_MIN
    bright = pixels[bright_mask]
    if bright.shape[0] < _BG_BRIGHT_OCCUPANCY * pixels.shape[0]:
        return None

    median = np.median(bright, axis=0)
    if median.min() > _BG_WHITE_MIN:
        return None
    if median.min() < _BG_DARK_MAX:
        return None

    return tuple(int(round(v)) for v in median)


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


_HALO_UNIFORM_TOL = 20  # halo 여부 무관, 행/열 픽셀이 중앙값과 "근사 같음"으로 볼 채널 편차 상한
_HALO_UNIFORM_OCCUPANCY = 0.85  # 근사 균일 판정에 필요한 최소 비율(둥근 모서리 등 소수 예외 허용)


def _chroma_span(row_chroma: np.ndarray, chroma_min: int) -> float:
    """행/열 1개 분량의 채도 배열에서 chroma_min 이상 픽셀의 비율."""
    return float((row_chroma >= chroma_min).mean())


def _is_uniform_line(line_rgb: np.ndarray, tol: int, occupancy: float = _HALO_UNIFORM_OCCUPANCY) -> bool:
    """행/열(line_rgb, shape (N, 3)) 픽셀들이 색상 무관하게 근사 균일한지 판정한다.

    실물 스캔의 halo 밴드는 둥근 모서리·안티앨리어싱 경계에서 바깥 여백색
    쪽으로 서서히 번져 나가는 픽셀이 섞여 있어(예: 왼쪽 halo 밴드를 위→아래로
    훑으면 상하 모서리 부근 몇 %는 위/아래쪽 변의 흰 여백이 비쳐 보임),
    엄격한 min-max나 백분위수 범위로는 소수 예외 때문에 오탐(실제로는
    균일한데 콘텐츠로 오판)하기 쉽다. 대신 채널별 중앙값과의 최대 편차가
    tol 이하인 픽셀 비율이 occupancy 이상이면 균일로 본다 — _uniform_bbox의
    occupancy 판정과 같은 접근이다.
    """
    median = np.median(line_rgb, axis=0)
    diff = np.abs(line_rgb - median).max(axis=1)
    return float((diff <= tol).mean()) >= occupancy


_SOFT_CHROMA_MIN = 15  # "유채색 균일 띠"로 볼 최소 채도 (검은/회색 선 보호 문턱)


def _edge_frame_depth(
    rgb_edge: np.ndarray,
    max_band: int,
    chroma_min: int,
    span_min: float,
    halo_allow: int,
) -> int:
    """rgb_edge(가장자리→안쪽으로 정렬된 RGB 배열, index 0이 바깥 가장자리)에서
    장식 프레임 밴드(halo 포함)의 깊이(px)를 반환한다.

    실물 책 프레임은 변마다 채도가 다르다(실측: 상단 66, 하단 34) — 고채도
    밴드(chroma_min·span_min)뿐 아니라 **배경과 다른 유채색(중앙값 채도
    ≥ _SOFT_CHROMA_MIN) 균일 행/열**도 프레임 구성 요소로 인정한다.
    검은/회색 표 테두리는 채도가 문턱 미만이라 "콘텐츠"로 분류돼 보호된다.

    판정 규칙 (라인 단위, 배경색 추정 불필요 — 프레임이 크롭 가장자리에 딱
    붙어 있으면 "바깥 2px 배경 추정"이 프레임 색으로 오염되므로 쓰지 않는다):
    - **배경 행** = 근사 균일 AND 중앙값 채도 < _SOFT_CHROMA_MIN (흰/회색 여백)
    - **프레임 성분 행** = 고채도 밴드(chroma_min·span_min) OR
      근사 균일 AND 중앙값 채도 ≥ _SOFT_CHROMA_MIN (halo·연한 프레임)
    - 프레임 성분을 1행 이상 본 뒤 배경 행을 만나면 → 거기까지의 깊이 반환
      ("배경(무채색 여백) 사이에 낀 유채색 띠"만 프레임으로 확정)
    - 그 외 행(콘텐츠)을 만나면 → 0 (이 변 포기)
    - 창을 소진하면 → 0 (전면 유채색 단색 이미지 등은 배경 재개가 없어 보호,
      검은/회색 선은 채도가 낮아 배경으로 분류되므로 saw_frame이 서지 않아 보호)

    rgb_edge는 이미 "이 변에서 바라본" 방향으로 정렬돼 있어야 한다 — 호출자가
    top/bottom/left/right에 맞게 배열을 뒤집거나 전치해 넘긴다.
    """
    window = max_band + halo_allow
    n = min(window + 1, rgb_edge.shape[0])
    chroma = rgb_edge.max(axis=2) - rgb_edge.min(axis=2)

    depth = 0
    saw_frame = False
    for i in range(n):
        line = rgb_edge[i]
        median = np.median(line, axis=0)
        med_chroma = int(median.max() - median.min())
        uniform = _is_uniform_line(line, _HALO_UNIFORM_TOL)

        if uniform and med_chroma < _SOFT_CHROMA_MIN:
            if saw_frame:
                return depth  # 프레임 띠가 끝나고 무채색 배경 재개 — 확정
            depth += 1  # 선두 배경 행 — 제거해도 무해 (마지막 트림이 어차피 정리)
            continue

        strong_band = _chroma_span(chroma[i], chroma_min) >= span_min
        colored_uniform = uniform and med_chroma >= _SOFT_CHROMA_MIN
        if strong_band or colored_uniform:
            saw_frame = True
            depth += 1
            continue

        return 0  # 콘텐츠 행 — 이 변 포기

    return 0  # 창 소진 — 배경 재개를 못 봤으면 프레임으로 확정하지 않는다


def expand_to_frame(
    page_img: Image.Image,
    box: tuple[int, int, int, int],
    max_expand: int = 300,
    inward: int = 60,
    chroma_min: int = 20,
    span_min: float = 0.55,
) -> tuple[int, int, int, int]:
    """크롭 박스를 감싸는 완전한 장식 프레임이 있으면 그 경계까지 맞춘다.

    책 다이어그램은 연보라 둥근 사각 테두리 박스 안에 들어 있고, 그 박스는
    본문 단 너비를 꽉 채우는 원본 디자인이다. Mistral bbox는 그림 콘텐츠에만
    맞으므로 이 테두리가 잘려 나온다.

    실측(page_0018 트림본)에서 드러난 핵심: 테두리는 **세로로는 이미 박스
    안쪽에 들어와 있고(위 587행/아래 795행 vs 박스 585~795), 가로로만 박스
    바깥**에 있었다. 그래서 "4변 모두 바깥으로 탐색"하는 방식으로는 위/아래를
    영영 못 찾는다. 대신 각 변마다 **박스 경계 안쪽 inward px부터 바깥
    max_expand px까지**를 훑어 프레임 선을 찾고, 가장 바깥 것을 택한다.

    프레임 선 판정: 그 행/열에서 채도 chroma_min 이상인 픽셀이 span_min 비율
    이상. 4변 모두에서 찾았을 때만 확장한다 -- 장식 프레임이 없는 사진 등을
    잘못 키우지 않기 위한 안전장치.

    Returns:
        완전한 프레임을 찾으면 그 바깥 경계 + 2px, 아니면 입력 box 그대로.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    if x1 <= x0 or y1 <= y0:
        return box

    rgb = _scan_rgb(page_img)
    h, w = rgb.shape[:2]
    chroma = rgb.max(axis=2) - rgb.min(axis=2)

    def row_is_frame(y: int, xa: int, xb: int) -> bool:
        if not (0 <= y < h) or xb <= xa:
            return False
        return float((chroma[y, xa:xb] >= chroma_min).mean()) >= span_min

    def col_is_frame(x: int, ya: int, yb: int) -> bool:
        if not (0 <= x < w) or yb <= ya:
            return False
        return float((chroma[ya:yb, x] >= chroma_min).mean()) >= span_min

    # 세로 프레임선은 박스 폭보다 넓을 수 있으므로, 가로 스캔 범위는 박스 폭을 쓴다
    top = next((y for y in range(max(0, y0 - max_expand), min(h, y0 + inward))
                if row_is_frame(y, x0, x1)), None)
    bottom = next((y for y in range(min(h - 1, y1 + max_expand), max(-1, y1 - inward), -1)
                   if row_is_frame(y, x0, x1)), None)
    if top is None or bottom is None or bottom <= top:
        return box

    # 좌우는 위에서 찾은 프레임의 세로 범위로 스캔해야 정확하다
    left = next((x for x in range(max(0, x0 - max_expand), min(w, x0 + inward))
                 if col_is_frame(x, top, bottom + 1)), None)
    right = next((x for x in range(min(w - 1, x1 + max_expand), max(-1, x1 - inward), -1)
                  if col_is_frame(x, top, bottom + 1)), None)
    if left is None or right is None or right <= left:
        return box

    pad = 2
    return (max(0, left - pad), max(0, top - pad),
            min(w, right + 1 + pad), min(h, bottom + 1 + pad))


_CORNER_WINDOW = 18  # 모서리 호 탐색 창 한 변(px)
_CORNER_MAX_AREA_FRAC = 0.6  # 창 대비 이 비율보다 큰 성분은 콘텐츠로 보고 보존


def _erase_corner_arcs(img: Image.Image, chroma_min: int = _SOFT_CHROMA_MIN) -> Image.Image:
    """직선 프레임 밴드를 벗겨낸 뒤 네 귀퉁이에 남는 둥근 모서리 호(arc)
    조각을 지운다.

    호는 행/열 점유율이 낮아 균일 트림(노이즈 취급)에도, 프레임 밴드 탐지
    (span 미달)에도 안 잡히는 사각지대다. 크롭 경계를 움직이는 대신, 각
    귀퉁이의 작은 창(_CORNER_WINDOW²) 안에서 이미지 테두리에 접한 소형
    유채색 연결 성분만 창 내 배경 중앙값 색으로 칠한다 — 콘텐츠 손실이
    구조적으로 불가능한 외과적 후처리.

    보호 장치: 성분이 창 면적의 _CORNER_MAX_AREA_FRAC를 넘으면 콘텐츠
    (전면 유채색 배경 크롭 등)로 보고 건드리지 않는다.
    """
    rgb = _scan_rgb(img)
    h, w = rgb.shape[:2]
    k = min(_CORNER_WINDOW, h // 2, w // 2)
    if k < 3:
        return img

    out = np.asarray(img.convert("RGB"), dtype=np.uint8).copy()
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    max_area = _CORNER_MAX_AREA_FRAC * k * k

    # (row 슬라이스, col 슬라이스, 창 좌표계에서 테두리에 해당하는 변)
    corners = [
        (slice(0, k), slice(0, k), ("top", "left")),
        (slice(0, k), slice(w - k, w), ("top", "right")),
        (slice(h - k, h), slice(0, k), ("bottom", "left")),
        (slice(h - k, h), slice(w - k, w), ("bottom", "right")),
    ]
    for rs, cs, edges in corners:
        window_mask = chroma[rs, cs] >= chroma_min
        if not window_mask.any():
            continue
        # 테두리 접촉 픽셀에서 시작하는 연결 성분 (4-이웃 BFS)
        kh, kw = window_mask.shape
        seeds = []
        if "top" in edges:
            seeds += [(0, x) for x in range(kw) if window_mask[0, x]]
        if "bottom" in edges:
            seeds += [(kh - 1, x) for x in range(kw) if window_mask[kh - 1, x]]
        if "left" in edges:
            seeds += [(y, 0) for y in range(kh) if window_mask[y, 0]]
        if "right" in edges:
            seeds += [(y, kw - 1) for y in range(kh) if window_mask[y, kw - 1]]
        if not seeds:
            continue
        visited = np.zeros_like(window_mask)
        stack = list(seeds)
        component = []
        while stack:
            y, x = stack.pop()
            if not (0 <= y < kh and 0 <= x < kw) or visited[y, x] or not window_mask[y, x]:
                continue
            visited[y, x] = True
            component.append((y, x))
            stack += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
        if not component or len(component) > max_area:
            continue  # 성분 없음 또는 콘텐츠급 대형 성분 — 보존
        # 창 내 비유채색 픽셀의 중앙값을 배경색으로 사용해 칠한다
        bg_pixels = rgb[rs, cs][~window_mask]
        fill = (
            np.median(bg_pixels, axis=0).astype(np.uint8)
            if bg_pixels.size
            else np.array([255, 255, 255], np.uint8)
        )
        base_y, base_x = rs.start, cs.start
        for y, x in component:
            out[base_y + y, base_x + x] = fill

    result = Image.fromarray(out)
    return result


def strip_chromatic_frame(
    img: Image.Image,
    max_band: int = 8,
    chroma_min: int = 40,
    span_min: float = 0.7,
    max_iter: int = 3,
    halo_allow: int = 6,
) -> Image.Image:
    """책 장식용 채도 높은 둥근 프레임 선을 그림 크롭 가장자리에서 제거한다.

    trim_uniform_margins는 배경색과 다른 픽셀이 섞인 행/열을 콘텐츠로 보고
    멈추므로, 4변 전체(또는 대부분)를 가로지르는 장식 프레임 선은 "콘텐츠"로
    오판되어 남는다. 이 함수는 가장자리 근처에서 채도(max(R,G,B)-min(R,G,B))가
    chroma_min 이상인 픽셀이 해당 행/열의 span_min 비율 이상을 차지하는
    연속 밴드를 변마다 찾아 제거한다.

    실물 스캔에서는 진짜 프레임 선 바깥쪽에 저채도 halo(안티앨리어싱 번짐)가
    붙어 있는 경우가 있다 — halo는 채도가 낮아(chroma_min 미만) 밴드로 잡히지
    않고, 동시에 배경색과의 채널 편차는 커서(tol 초과) 균일 트림으로도
    지워지지 않아 프레임 제거가 막힌다. 이를 위해 가장자리부터 탐색 창
    (max_band + halo_allow px) 안에서, 색상 무관 근사 균일한 행/열(halo 포함)은
    건너뛰면서 채도 밴드를 찾는다 — 밴드를 찾으면 가장자리부터 밴드 끝까지
    (halo 포함) 한 번에 제거한다. 자세한 스캔 로직은 _edge_frame_depth 참고.

    검은/회색(무채색) 선은 채도가 0에 가까워 chroma_min을 넘지 못하므로
    이 로직으로는 절대 제거되지 않는다 — 표 등 실제 콘텐츠 테두리 보호.

    알려진 한계 (의도된 트레이드오프): 크롭 가장자리에 딱 붙은 얇은(≤max_band)
    유채색 콘텐츠 밴드(예: 컬러 구분선)가 뒤따르는 무채색 여백과 함께 있으면
    장식 프레임과 구별할 수 없어 함께 제거될 수 있다. 두께 상한(max_band)이
    컬러 헤더 등 두꺼운 콘텐츠를 보호하며, bbox 외측 패딩(pad_out) 덕분에
    실제 콘텐츠가 크롭 가장자리에 정확히 붙는 경우 자체가 드물다.

    각 반복은: ① 현재 콘텐츠 bbox를 pad=0 기준으로 재계산(프레임 제거로
    새로 드러난 여백을 다음 반복에서 마저 정리) ② 4변에서 채도 밴드 탐지
    후 bbox를 안쪽으로 좁힌다. 어느 변에서도 밴드가 제거되지 않으면 반복을
    종료한다. bbox 계산은 원본 이미지 좌표계에서 좌표만 갱신할 뿐 실제로
    이미지를 자르지 않는다 — 마지막에 pad=2를 적용할 때 원본의 실제 배경
    픽셀을 그대로 활용하기 위함이다(중간에 pad=0으로 물리적으로 잘라내면
    바깥 여백 픽셀이 사라져 마지막 pad=2가 복원할 배경이 없어진다).

    Args:
        img: 입력 이미지 (모드 무관 — 채도 판정은 RGB 변환 후 수행)
        max_band: 변당 탐지할 최대 밴드(halo 제외, 프레임 자체) 두께(px)
        chroma_min: 프레임 픽셀로 판정할 최소 채도
        span_min: 밴드로 판정할 행/열 내 채도 픽셀 비율 임계치(0~1)
        max_iter: 반복 상한
        halo_allow: 프레임 밴드 앞에 허용할 halo(근사 균일 저채도 구간)
            최대 두께(px) — 탐색 창은 max_band + halo_allow

    Returns:
        프레임이 제거되고 pad=2로 트림된 이미지. 프레임이 없으면
        trim_uniform_margins(img, pad=2)와 동일한 결과.
    """
    w0, h0 = img.size
    # 절대 좌표(원본 img 기준) 콘텐츠 bbox. 처음엔 이미지 전체.
    left, top, right, bottom = 0, 0, w0 - 1, h0 - 1
    # 프레임을 벗겨낸 변의 경계 — 마지막 pad가 이 선을 넘어 프레임을
    # 되물지 않도록 하는 하드 바운더리 (실물의 둥근 모서리 잔여물 대응)
    hard_l, hard_t, hard_r, hard_b = 0, 0, w0 - 1, h0 - 1
    stripped_any = False  # 밴드를 실제로 벗긴 경우에만 모서리 호 지우개를 돌린다
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

        # ② 좁혀진 영역의 4변에서 (halo 뒤에 숨은 것 포함) 채도 밴드 탐지
        sub = img.crop((left, top, right + 1, bottom + 1))
        rgb = _scan_rgb(sub)
        h, w = rgb.shape[:2]

        d_top = _edge_frame_depth(rgb, max_band, chroma_min, span_min, halo_allow)
        d_bottom = _edge_frame_depth(rgb[::-1, :, :], max_band, chroma_min, span_min, halo_allow)
        d_left = _edge_frame_depth(rgb.transpose(1, 0, 2), max_band, chroma_min, span_min, halo_allow)
        d_right = _edge_frame_depth(
            rgb[:, ::-1, :].transpose(1, 0, 2), max_band, chroma_min, span_min, halo_allow
        )

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
        stripped_any = True
        # 벗겨낸 변은 하드 바운더리 갱신 — 마지막 pad가 이 안쪽까지만 확장 가능
        if d_top:
            hard_t = top
        if d_bottom:
            hard_b = bottom
        if d_left:
            hard_l = left
        if d_right:
            hard_r = right

    # ③ 마지막 pad=2 — 원본 img에서 콘텐츠 bbox 주변 실제 배경 픽셀을 포함해
    # 다시 크롭한 뒤 trim_uniform_margins로 정리한다(안전장치 min_keep 등 재사용).
    pad = 2
    fl = max(hard_l, left - pad)
    ft = max(hard_t, top - pad)
    fr = min(hard_r, right + pad)
    fb = min(hard_b, bottom + pad)
    final_sub = img.crop((fl, ft, fr + 1, fb + 1))
    result = trim_uniform_margins(final_sub, pad=pad)
    # 모서리 호는 프레임 밴드의 부속물 — 밴드를 벗긴 크롭에서만 지운다.
    # (무조건 돌리면 프레임 없는 그림의 모서리에 걸친 컬러 콘텐츠까지 지울 위험)
    if stripped_any:
        result = _erase_corner_arcs(result)
    return result
