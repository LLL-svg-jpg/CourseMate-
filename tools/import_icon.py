"""把一张图片转成 CourseMate 的应用图标。

用法：
    python tools/import_icon.py 你的图片.png
    python tools/import_icon.py 你的图片.png --keep-bg   # 保留原背景不做透明处理

默认会去掉白色背景。原因：应用图标要显示在深浅不一的底色上
（任务栏、标题栏、深色主题），带白色方块会非常突兀。

去背景用的是从四角漫延的填充，而不是"所有白像素都删掉"——
后者会把羽毛内部的白色高光一并挖空，图案就破了。

生成后重新打包即可让 exe 用上新图标：
    python build.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets"
CANVAS = 512          # 处理时的工作尺寸，够大以保留细节
MARGIN_RATIO = 0.04   # 四周留白，图标贴边会被系统裁掉
WHITE_THRESHOLD = 26  # 与纯白的最大偏差，超过就认为是图案本体


def strip_background(img: Image.Image) -> Image.Image:
    """从四角漫延，把连通的白色背景变透明。"""
    img = img.convert("RGBA")
    width, height = img.size
    pixels = img.load()

    def is_white(xy: tuple[int, int]) -> bool:
        r, g, b, a = pixels[xy]
        if a == 0:
            return True
        return (255 - r) <= WHITE_THRESHOLD and (255 - g) <= WHITE_THRESHOLD \
            and (255 - b) <= WHITE_THRESHOLD

    # 广度优先，从四条边上的白色像素开始扩散
    stack: list[tuple[int, int]] = []
    seen = bytearray(width * height)
    for x in range(width):
        for y in (0, height - 1):
            if is_white((x, y)):
                stack.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if is_white((x, y)):
                stack.append((x, y))

    while stack:
        x, y = stack.pop()
        idx = y * width + x
        if seen[idx]:
            continue
        seen[idx] = 1
        if not is_white((x, y)):
            continue
        pixels[x, y] = (255, 255, 255, 0)
        if x > 0:
            stack.append((x - 1, y))
        if x < width - 1:
            stack.append((x + 1, y))
        if y > 0:
            stack.append((x, y - 1))
        if y < height - 1:
            stack.append((x, y + 1))
    return img


def soften_edges(img: Image.Image) -> Image.Image:
    """把 alpha 通道轻微模糊，消除去背景留下的硬锯齿。"""
    alpha = img.getchannel("A").filter(ImageFilter.GaussianBlur(0.6))
    img.putalpha(alpha)
    return img


def trim_and_center(img: Image.Image) -> Image.Image:
    """裁掉透明边框，等比缩放并居中，四周留少量空白。"""
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    margin = int(CANVAS * MARGIN_RATIO)
    inner = CANVAS - margin * 2
    w, h = img.size
    scale = min(inner / w, inner / h)
    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.paste(img, ((CANVAS - img.width) // 2, (CANVAS - img.height) // 2), img)
    return canvas


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep_bg = "--keep-bg" in sys.argv
    if not args:
        print(__doc__)
        return 1

    src = Path(args[0]).expanduser()
    if not src.exists():
        print(f"找不到文件：{src}")
        return 1

    print(f"读取 {src}")
    img = Image.open(src).convert("RGBA")
    print(f"原始尺寸 {img.width}x{img.height}")

    if not keep_bg:
        img = strip_background(img)
        img = soften_edges(img)
        opaque = sum(1 for a in img.getchannel("A").tobytes() if a > 200)
        ratio = opaque / (img.width * img.height)
        print(f"已去除白色背景，图案占比 {ratio:.1%}")
        if ratio > 0.92:
            print("  提示：几乎没去掉什么，原图可能本来就没有白底，或底色不是纯白。")

    img = trim_and_center(img)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    img.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "app.png")
    img.resize((64, 64), Image.LANCZOS).save(OUT_DIR / "app_64.png")
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
             (48, 48), (64, 64), (128, 128), (256, 256)]
    img.resize((256, 256), Image.LANCZOS).save(
        OUT_DIR / "app.ico", format="ICO", sizes=sizes)

    corner = img.getpixel((1, 1))
    print(f"\n已生成：")
    print(f"  {OUT_DIR / 'app.ico'}  ({len(sizes)} 种尺寸)")
    print(f"  {OUT_DIR / 'app.png'}")
    print(f"  {OUT_DIR / 'app_64.png'}")
    print(f"左上角像素 {corner}" + ("  背景透明 ✓" if corner[3] == 0 else "  注意：背景不透明"))
    print("\n接下来运行 python build.py 重新打包，exe 就会用上新图标。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
