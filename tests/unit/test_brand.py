from importlib.resources import files
from io import BytesIO

from PIL import Image


def test_brand_assets_include_transparent_small_icons_and_windows_sizes():
    assets = files("xilogue").joinpath("assets")
    for size in (16, 32, 48, 128, 256):
        image = Image.open(BytesIO(assets.joinpath(f"icon-{size}.png").read_bytes()))
        assert image.size == (size, size)
        assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0
    ico = Image.open(BytesIO(assets.joinpath("icon.ico").read_bytes()))
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= ico.ico.sizes()
