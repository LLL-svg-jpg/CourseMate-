"""首次启动截图工具的复制边界与画面对比回归。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.first_launch_visual import ai_labels_visible, changed_fraction, copy_program


with TemporaryDirectory() as temp:
    root = Path(temp)
    source = root / "source"
    (source / "_internal").mkdir(parents=True)
    (source / "CourseMate.exe").write_bytes(b"test executable")
    (source / "_internal" / "library.bin").write_bytes(b"dependency")
    (source / "config.toml").write_text("private", encoding="utf-8")
    (source / "runtime").mkdir()
    (source / "runtime" / "cookies.json").write_text("private", encoding="utf-8")
    copied = copy_program(source, root / "first")
    assert (copied / "CourseMate.exe").is_file()
    assert (copied / "_internal" / "library.bin").is_file()
    assert not (copied / "config.toml").exists()
    assert not (copied / "runtime").exists()

    (source / "_internal" / "answers.db").write_bytes(b"private")
    try:
        copy_program(source, root / "second")
    except RuntimeError:
        pass
    else:
        raise AssertionError("依赖目录中的个人数据没有阻止复制")
    assert not (root / "second").exists()

black = Image.new("RGB", (100, 100), "black")
white = Image.new("RGB", (100, 100), "white")
assert changed_fraction(black, black) == 0
assert changed_fraction(black, white) == 1
assert not ai_labels_visible(white)
with_labels = Image.new("RGB", (1320, 1000), "white")
for x0, y0, x1, y1 in ((35, 220, 118, 270), (35, 285, 118, 340),
                       (35, 365, 118, 400), (35, 430, 118, 470)):
    for x in range(x0, x0 + 10):
        for y in range(y0, y0 + 10):
            with_labels.putpixel((x, y), (0, 0, 0))
assert ai_labels_visible(with_labels)
print("隔离复制、个人数据拦截和画面对比：通过")
