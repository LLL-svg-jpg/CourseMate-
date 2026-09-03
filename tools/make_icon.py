"""生成 CourseMate 的羽毛图标。

产出透明背景的 ICO（多尺寸）与 PNG。
用代码画而不是放一张位图，是为了在 16×16 这种小尺寸下也能保持清晰——
直接缩放大图会糊成一团色块。

运行：python tools/make_icon.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "assets"
SUPER = 8  # 超采样倍数，先画大图再缩小，得到平滑边缘
BASE = 256
CANVAS = BASE * SUPER

# 青蓝渐变，从羽根到羽尖
COLOR_ROOT = (34, 106, 138)
COLOR_TIP = (104, 205, 224)
COLOR_SHAFT = (18, 74, 100)


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def shaft_point(t: float) -> tuple[float, float]:
    """羽轴上参数 t（0=羽根，1=羽尖）处的坐标。

    轴带一点弧度，笔直的羽毛看着像叶子。
    """
    x = 0.50 + 0.13 * math.sin(t * math.pi * 0.85) - 0.06 * t
    y = 0.92 - 0.84 * t
    return x * CANVAS, y * CANVAS


def half_width(t: float) -> float:
    """羽毛在 t 处的半宽。羽根收窄、中段最宽、羽尖收成锐角。"""
    if t < 0.18:                       # 光秃的羽柄，羽毛下端没有羽枝
        return 0.0
    s = (t - 0.18) / 0.82
    # 靠根部迅速展开、中段最宽、尖端收拢
    w = math.sin(s * math.pi) ** 0.70
    return (0.010 + 0.115 * w) * CANVAS * (1.0 - 0.20 * s)


def _axis_frame(t: float) -> tuple[float, float, float, float, float, float]:
    """返回羽轴在 t 处的位置、切向、法向。"""
    x, y = shaft_point(t)
    x2, y2 = shaft_point(min(1.0, t + 0.004))
    dx, dy = x2 - x, y2 - y
    length = math.hypot(dx, dy) or 1.0
    tx, ty = dx / length, dy / length
    return x, y, tx, ty, -ty, tx


def draw_feather(img: Image.Image) -> None:
    """用一根根羽枝拼出羽毛。

    实心填充画出来是叶子；羽毛的识别特征是羽枝彼此分离、
    边缘参差不齐，所以这里逐根画羽枝，而不是填一个闭合轮廓。
    """
    draw = ImageDraw.Draw(img)

    # 羽枝：数量足够密才连成片，又要留出缝隙才像羽毛
    barb_count = 105
    barb_width = max(2, int(CANVAS * 0.0062))

    for i in range(barb_count):
        t = 0.10 + (i / (barb_count - 1)) * 0.88
        x, y, tx, ty, nx, ny = _axis_frame(t)
        w = half_width(t)

        # 末端长度做规律性微扰，让边缘参差而不是一条光滑弧线
        jitter = 1.0 + 0.075 * math.sin(i * 2.399) + 0.045 * math.sin(i * 5.117)
        # 羽枝朝羽尖倾斜，越靠近尖端倾角越大——真羽毛就是这样
        tilt = 0.30 + 0.45 * t
        color = lerp(COLOR_ROOT, COLOR_TIP, t) + (255,)

        for side in (1, -1):
            reach = w * jitter * (1.0 if side > 0 else 0.93)  # 两侧略不对称
            ex = x + nx * reach * side + tx * reach * tilt
            ey = y + ny * reach * side + ty * reach * tilt
            draw.line([(x, y), (ex, ey)], fill=color, width=barb_width)

    # 羽轴：压在羽枝之上，从羽根一直延伸到羽尖，根部露出一截光杆
    steps = 600
    for i in range(steps):
        t0, t1 = i / steps, (i + 1) / steps
        p0, p1 = shaft_point(t0), shaft_point(t1)
        width = max(2, int(CANVAS * 0.0135 * (1.0 - t0 * 0.8)))
        draw.line([p0, p1], fill=COLOR_SHAFT + (255,), width=width)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))  # 全透明底
    draw_feather(img)

    master = img.resize((BASE, BASE), Image.LANCZOS)
    png_path = OUT_DIR / "app.png"
    master.save(png_path)

    # ICO 内嵌多个尺寸：Windows 会按场景挑合适的那个
    # （任务栏 32、标题栏 16、快捷方式 48、资源管理器大图标 256）
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
             (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_path = OUT_DIR / "app.ico"
    master.save(ico_path, format="ICO", sizes=sizes)

    # tkinter 的 iconphoto 要 PNG，单独存一份 64px 的
    master.resize((64, 64), Image.LANCZOS).save(OUT_DIR / "app_64.png")

    print(f"已生成 {ico_path}（{len(sizes)} 种尺寸）")
    print(f"已生成 {png_path}")
    corner = master.getpixel((2, 2))
    print(f"左上角像素 {corner} —— alpha=0 表示背景确为透明")


if __name__ == "__main__":
    main()
