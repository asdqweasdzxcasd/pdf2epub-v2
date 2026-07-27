"""imgproc.trim_uniform_margins / strip_chromatic_frame 단위 테스트"""
import numpy as np
from PIL import Image, ImageDraw

from app.pipeline.imgproc import strip_chromatic_frame, trim_uniform_margins


def _make_margin_image(bg=(255, 255, 255), fg=(0, 0, 0), size=200, box=(50, 50, 149, 149)):
    """균일 배경 위에 사각형 콘텐츠가 있는 이미지를 만든다."""
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle(box, fill=fg)
    return img


def test_흰_여백이_트림되고_pad만큼_남는다():
    img = _make_margin_image()
    trimmed = trim_uniform_margins(img)  # tol=12, pad=8 기본값

    # 콘텐츠 박스(50,50)-(149,149) + pad 8 = (42,42)-(157,157) -> 116x116
    assert trimmed.size == (116, 116)


def test_트림된_이미지의_콘텐츠가_보존된다():
    img = _make_margin_image()
    trimmed = trim_uniform_margins(img)

    # 트림된 이미지 중심은 원래 검은 사각형이었으므로 여전히 검정
    cx, cy = trimmed.size[0] // 2, trimmed.size[1] // 2
    assert trimmed.getpixel((cx, cy)) == (0, 0, 0)

    # pad 경계 부근(트림 이미지의 좌상단 근처)은 배경색(흰색)이어야 함
    assert trimmed.getpixel((1, 1)) == (255, 255, 255)


def test_어두운_배경에서도_동작한다():
    img = _make_margin_image(bg=(10, 10, 10), fg=(240, 240, 240))
    trimmed = trim_uniform_margins(img)

    assert trimmed.size == (116, 116)
    cx, cy = trimmed.size[0] // 2, trimmed.size[1] // 2
    assert trimmed.getpixel((cx, cy)) == (240, 240, 240)


def test_tol_이내_노이즈는_배경으로_간주된다():
    # 배경에 약한 노이즈(채널 최대 편차 <= tol=12)가 섞여 있어도 여백으로 처리
    img = _make_margin_image()
    px = img.load()
    px[0, 0] = (245, 245, 245)  # diff 10
    px[199, 199] = (244, 250, 246)  # diff 11
    trimmed = trim_uniform_margins(img)
    assert trimmed.size == (116, 116)


def test_min_keep_안전장치_과잉_트림시_원본_반환():
    # 작은 점(5x5)만 있는 이미지: pad=8 기본값으로도 트림 결과 면적이
    # 원본의 min_keep(0.25) 미만이 되어 원본 그대로 반환돼야 한다
    img = _make_margin_image(box=(97, 97, 102, 102))
    trimmed = trim_uniform_margins(img)
    assert trimmed.size == img.size
    assert trimmed.tobytes() == img.tobytes()


def test_콘텐츠_없는_균일_이미지는_원본_반환():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    trimmed = trim_uniform_margins(img)
    assert trimmed.size == (100, 100)


def test_pad_파라미터_조정():
    img = _make_margin_image()
    trimmed = trim_uniform_margins(img, pad=2)
    # (50,50)-(149,149) + pad 2 = (48,48)-(151,151) -> 104x104
    assert trimmed.size == (104, 104)


def test_min_keep_파라미터_조정으로_작은_콘텐츠도_트림():
    img = _make_margin_image(box=(97, 97, 102, 102))
    # min_keep을 아주 낮게 주면 작은 콘텐츠도 트림 허용
    trimmed = trim_uniform_margins(img, min_keep=0.0)
    assert trimmed.size != img.size
    assert trimmed.size[0] < img.size[0]


# --- finding 1: 알파 채널 무시 ---


def test_투명_여백은_흰_배경에_합성되어_트림된다():
    """여백이 완전 투명(alpha=0)이지만 내부 RGB 값이 위치마다 들쭉날쭉한
    "가비지 컬러"(일부 렌더러가 실제로 이렇게 만든다)여도, 알파를 무시하고
    그대로 RGB 변환해 스캔하면 여백 전체가 노이즈투성이 콘텐츠로 오인된다.
    알파 합성(흰 배경) 후 스캔하면 이런 오염과 무관하게 정상 트림돼야 한다.
    """
    size = 200
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[..., 0] = np.arange(size) % 256  # 열마다 달라지는 가비지 R
    arr[..., 1] = (np.arange(size) * 3) % 256  # 가비지 G (행 방향)
    arr[..., 2] = 128
    arr[..., 3] = 0  # 완전 투명
    arr[50:150, 50:150] = (0, 0, 0, 255)  # 불투명 검정 콘텐츠 박스
    img = Image.fromarray(arr, mode="RGBA")

    trimmed = trim_uniform_margins(img)

    assert trimmed.size == (116, 116)
    assert trimmed.mode == "RGBA"
    cx, cy = trimmed.size[0] // 2, trimmed.size[1] // 2
    assert trimmed.getpixel((cx, cy)) == (0, 0, 0, 255)


def test_LA_모드_이미지도_알파_합성_후_트림된다():
    size = 200
    arr = np.zeros((size, size, 2), dtype=np.uint8)
    arr[..., 0] = np.arange(size) % 256  # 가비지 명도
    arr[..., 1] = 0  # 완전 투명
    arr[50:150, 50:150] = (0, 255)  # 불투명 검정 콘텐츠
    img = Image.fromarray(arr, mode="LA")

    trimmed = trim_uniform_margins(img)

    assert trimmed.size == (116, 116)
    assert trimmed.mode == "LA"


# --- finding 2: 고립 노이즈 픽셀이 행/열 전체를 콘텐츠로 오판 ---


def test_고립된_노이즈_픽셀은_트림에_영향을_주지_않는다():
    img = _make_margin_image()
    px = img.load()
    # 서로 다른 행/열에 흩어진 고립 노이즈 픽셀 (tol 초과, 스캔 먼지 시뮬레이션)
    for x, y in [(5, 5), (10, 190), (195, 15), (3, 100), (198, 198)]:
        px[x, y] = (0, 0, 0)
    trimmed = trim_uniform_margins(img)
    # 기본 occupancy(0.005)에서는 고립 픽셀 1개(비율 1/200=0.005)가 무시돼
    # 여전히 정상 트림 결과(116x116)가 나와야 한다
    assert trimmed.size == (116, 116)


def test_연속된_콘텐츠는_occupancy_기준을_넘어_보존된다():
    img = _make_margin_image()
    draw = ImageDraw.Draw(img)
    # 여백 안(메인 박스 밖)에 작은 선분 모양 콘텐츠(5x21px, 고립 노이즈가
    # 아닌 연속 픽셀 뭉치) 추가 — 행/열 occupancy 둘 다 기본 임계치(0.005)를
    # 넘으므로 트림 경계가 이 콘텐츠까지 확장돼 보존돼야 한다
    draw.rectangle((15, 10, 19, 30), fill=(0, 0, 0))
    trimmed = trim_uniform_margins(img)
    assert trimmed.size[0] > 116
    assert trimmed.size[1] > 116


def test_occupancy_파라미터로_민감도_조정_가능():
    img = _make_margin_image()
    px = img.load()
    px[5, 5] = (0, 0, 0)  # 고립 노이즈 1픽셀
    # occupancy=0.0이면 예전처럼 단일 픽셀도 콘텐츠로 인식 -> 트림 경계 확장
    trimmed = trim_uniform_margins(img, occupancy=0.0)
    assert trimmed.size != (116, 116)


# --- strip_chromatic_frame ---


def _make_framed_diagram(
    size=200,
    frame_color=(160, 30, 200),
    frame_width=3,
    corner_gap=10,
    diagram_box=(60, 60, 139, 139),
):
    """흰 배경 + 가장자리 채도 프레임(둥근 모서리 흉내로 모서리 근처 몇 px 끊김)
    + 내부 다이어그램(검은 사각형)이 있는 합성 이미지.

    프레임은 이미지 진짜 가장자리(0, size-1)에 그려진다 — 책 장식 테두리가
    그림 크롭의 bbox 바깥 여백 없이 크롭 경계에 바로 물려 나오는 실측 상황을
    흉내낸다.
    """
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    lo, hi = corner_gap, size - 1 - corner_gap
    draw.rectangle((lo, 0, hi, frame_width - 1), fill=frame_color)  # top
    draw.rectangle((lo, size - frame_width, hi, size - 1), fill=frame_color)  # bottom
    draw.rectangle((0, lo, frame_width - 1, hi), fill=frame_color)  # left
    draw.rectangle((size - frame_width, lo, size - 1, hi), fill=frame_color)  # right
    draw.rectangle(diagram_box, fill=(0, 0, 0))
    return img


def _max_chroma(img: Image.Image) -> int:
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    return int((arr.max(axis=2) - arr.min(axis=2)).max())


def _black_pixel_count(img: Image.Image) -> int:
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    return int(np.all(arr == 0, axis=2).sum())


def test_채도_높은_프레임이_제거되고_다이어그램은_완전_보존된다():
    img = _make_framed_diagram()
    result = strip_chromatic_frame(img)

    # 보라 프레임(채도 170)이 완전히 사라져야 함 — 잔존 최대 채도는
    # chroma_min(40) 미만이어야 한다
    assert _max_chroma(result) < 40
    # 내부 검은 다이어그램(80x80=6400px)은 한 픽셀도 잘리지 않아야 한다
    assert _black_pixel_count(result) == 6400


def test_검은_프레임은_채도가_낮아_제거되지_않는다():
    # 채도 0인(R=G=B) 어두운 회색 프레임 — 표 등의 검은 테두리 시뮬레이션
    img = _make_framed_diagram(frame_color=(20, 20, 20))
    result = strip_chromatic_frame(img)

    # 프레임 색이 여전히 이미지 안에 남아 있어야 한다(제거되지 않음)
    arr = np.asarray(result.convert("RGB"), dtype=np.int16)
    assert np.any(np.all(arr == (20, 20, 20), axis=2))


def test_프레임_없는_이미지는_trim_pad2와_동일하다():
    img = _make_margin_image()
    result = strip_chromatic_frame(img)
    expected = trim_uniform_margins(img, pad=2)
    assert result.size == expected.size
    assert result.tobytes() == expected.tobytes()


# --- finding: halo 뒤에 숨은 채도 프레임 (실물 진단 page_0002_blk_001.png 재현) ---


def _make_haloed_framed_diagram(
    size=200,
    white=(255, 255, 255),
    halo_color=(235, 218, 234),  # 채도 17 — chroma_min(40) 미만
    frame_color=(195, 129, 191),  # 채도 66 — 진짜 프레임
    white_width=2,
    halo_width=3,
    frame_width=3,
    diagram_box=(60, 60, 139, 139),
):
    """가장자리부터 흰 여백 → 균일 halo(연분홍, 저채도) → 진짜 채도 프레임
    → 흰 여백 → 검은 콘텐츠 순으로 겹겹이 채운 동심 사각 프레임(4변 모두).

    바깥에서 안쪽으로 점점 작은 사각형을 덧칠하는 방식이라 4변 모두 같은
    두께의 링이 자연스럽게 만들어진다(모서리 특수 처리 불필요).
    halo는 배경(흰색)과의 채널 최대 편차(37)가 trim_uniform_margins의
    tol(12)을 넘어 균일 트림으로도 안 지워지고, 채도(17)가 chroma_min(40)
    미만이라 기존 채도 밴드 탐지에도 걸리지 않아 실물에서 프레임 제거가
    막히는 상황을 재현한다.
    """
    img = Image.new("RGB", (size, size), white)
    draw = ImageDraw.Draw(img)
    d1 = white_width
    d2 = white_width + halo_width
    d3 = white_width + halo_width + frame_width
    draw.rectangle((d1, d1, size - 1 - d1, size - 1 - d1), fill=halo_color)
    draw.rectangle((d2, d2, size - 1 - d2, size - 1 - d2), fill=frame_color)
    draw.rectangle((d3, d3, size - 1 - d3, size - 1 - d3), fill=white)
    draw.rectangle(diagram_box, fill=(0, 0, 0))
    return img


def test_halo_뒤에_숨은_채도_프레임이_4변_모두_제거된다():
    halo_color = (235, 218, 234)
    frame_color = (195, 129, 191)
    img = _make_haloed_framed_diagram(halo_color=halo_color, frame_color=frame_color)
    result = strip_chromatic_frame(img)

    arr = np.asarray(result.convert("RGB"), dtype=np.int16)
    # halo, 프레임 색 모두 결과에서 완전히 사라져야 한다(4변 모두)
    assert not np.any(np.all(arr == halo_color, axis=2))
    assert not np.any(np.all(arr == frame_color, axis=2))
    # 내부 검은 콘텐츠(80x80=6400px)는 한 픽셀도 잘리지 않아야 한다
    assert _black_pixel_count(result) == 6400


def test_저채도_균일_프레임도_제거된다():
    """실물 하단 프레임 재현: 채도 34짜리 연분홍 균일 띠는 chroma_min(40)
    문턱을 못 넘지만, '배경과 다른 유채색 균일 띠'로는 판정돼 제거돼야 한다."""
    img = _make_haloed_framed_diagram(
        halo_color=(235, 218, 234),
        frame_color=(215, 181, 212),  # 채도 34 — 기존 채도 밴드 기준 미달
    )
    result = strip_chromatic_frame(img)
    arr = np.asarray(result.convert("RGB"), dtype=np.int16)
    assert not np.any(np.all(arr == (215, 181, 212), axis=2))
    assert not np.any(np.all(arr == (235, 218, 234), axis=2))
    assert _black_pixel_count(result) == 6400


def test_한_변만_프레임이_있어도_pad가_프레임을_되물지_않는다():
    """top에만 halo+프레임이 있는 경우: 마지막 pad=2가 방금 벗겨낸 프레임
    픽셀을 다시 포함하면 안 된다 (실물에서 상단 2px 분홍선 잔존 원인)."""
    size = 120
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 2, size - 1, 4), fill=(235, 218, 234))   # halo 3px
    draw.rectangle((0, 5, size - 1, 7), fill=(195, 144, 191))   # 프레임 3px
    draw.rectangle((30, 40, 89, 99), fill=(0, 0, 0))            # 콘텐츠 60x60
    result = strip_chromatic_frame(img)
    arr = np.asarray(result.convert("RGB"), dtype=np.int16)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    assert not np.any(chroma >= 15), "유채색(프레임/halo) 픽셀이 결과에 남음"
    assert _black_pixel_count(result) == 3600


def test_모서리_호_잔여물이_지워진다():
    """실물 재현: 직선 프레임 밴드(halo 포함) + 둥근 모서리 호 → 밴드 스트립 후
    남는 호까지 지워지고 콘텐츠는 보존. (호 지우개는 밴드를 벗긴 크롭에서만 동작)"""
    size = 200
    img = _make_haloed_framed_diagram()
    draw = ImageDraw.Draw(img)
    pink = (195, 144, 191)
    # 프레임의 둥근 모서리 호 (밴드 스트립 후 잔여물이 되는 부분)
    draw.arc((0, 0, 30, 30), 180, 270, fill=pink, width=3)
    draw.arc((size - 31, 0, size - 1, 30), 270, 360, fill=pink, width=3)
    draw.arc((0, size - 31, 30, size - 1), 90, 180, fill=pink, width=3)
    draw.arc((size - 31, size - 31, size - 1, size - 1), 0, 90, fill=pink, width=3)
    result = strip_chromatic_frame(img)
    arr = np.asarray(result.convert("RGB"), dtype=np.int16)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    assert not np.any(chroma >= 15), "모서리 호 잔여물이 남음"
    assert _black_pixel_count(result) == 6400


def test_전면_유채색_콘텐츠는_모서리_지우기가_건드리지_않는다():
    """크롭 전체가 보라색 배경(실물 blk_000 유형): 귀퉁이 성분이 창을 가득
    채우므로 크기 가드에 걸려 아무것도 지워지지 않아야 한다."""
    img = Image.new("RGB", (100, 300), (176, 108, 170))
    ImageDraw.Draw(img).rectangle((30, 100, 69, 199), fill=(0, 0, 0))
    result = strip_chromatic_frame(img)
    arr = np.asarray(result.convert("RGB"), dtype=np.int16)
    assert np.any(np.all(arr == (176, 108, 170), axis=2)), "전면 유채색 배경이 지워짐"


def test_프레임_없는_크롭의_모서리_컬러_콘텐츠는_보존된다():
    """Codex P1 반영: 프레임 밴드가 없는 크롭은 모서리 호 지우개가 돌지 않아야
    한다 — 모서리에 걸친 컬러 로고/스트로크가 정상 콘텐츠인 경우."""
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    logo = (200, 60, 60)
    draw.ellipse((2, 2, 14, 14), fill=logo)  # 좌상단 모서리에 걸친 작은 컬러 로고
    draw.rectangle((60, 60, 139, 139), fill=(0, 0, 0))
    result = strip_chromatic_frame(img)
    arr = np.asarray(result.convert("RGB"), dtype=np.int16)
    assert np.any(np.all(arr == logo, axis=2)), "프레임 없는 크롭의 모서리 콘텐츠가 지워짐"
