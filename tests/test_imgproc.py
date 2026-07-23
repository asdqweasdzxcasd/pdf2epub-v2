"""imgproc.trim_uniform_margins 단위 테스트"""
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
