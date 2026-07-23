"""imgproc.trim_uniform_margins 단위 테스트"""
import numpy as np
from PIL import Image, ImageDraw

from app.pipeline.imgproc import trim_uniform_margins


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
