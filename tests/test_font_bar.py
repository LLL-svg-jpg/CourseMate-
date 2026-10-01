"""字号栏的交互、字号联动和小窗口边界回归；EXE 绘制仍需实际截图核对。"""
from pathlib import Path
import ctypes
import sys
import tempfile
import tkinter as tk
from tkinter import font, ttk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.gui import CourseMateGUI


if sys.platform == "win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(1)


def flush(root):
    root.update_idletasks()
    root.update()


def size_of(root, widget):
    return font.Font(root=root, font=widget.cget("font")).actual("size")


with tempfile.TemporaryDirectory() as temp:
    data = Path(temp)
    with patch("coursemate.gui.CONFIG_PATH", data / "config.toml"), \
         patch("coursemate.gui.app_dir", lambda: data), \
         patch.object(CourseMateGUI, "_start_tray"):
        for launch in (1, 2):
            root = tk.Tk()
            try:
                root.tk.call("tk", "scaling", 4 / 3)
                app = CourseMateGUI(root)
                notebook = next(w for w in app.body.winfo_children() if isinstance(w, ttk.Notebook))
                notebook.select(2)
                flush(root)
                assert int(round(app.font_size_var.get())) == (15 if launch == 1 else 18)
                controls = (app.font_field_label, app.font_minus, app.font_scale,
                            app.font_plus, app.font_size_label)
                for size in (10, 15, 24):
                    app.font_size_var.set(size)
                    app._apply_font_size()
                    for geometry in ("1000x700", "1320x1000"):
                        root.geometry(geometry)
                        flush(root)
                        page = app.setting_pages["界面"]
                        for widget in controls:
                            assert widget.winfo_ismapped()
                            x = widget.winfo_rootx() - page.winfo_rootx()
                            y = widget.winfo_rooty() - page.winfo_rooty()
                            assert 0 <= x and x + widget.winfo_width() <= page.winfo_width()
                            assert 0 <= y and y + widget.winfo_height() <= page.winfo_height()
                        for widget in (app.font_field_label, app.font_minus, app.font_plus,
                                       app.font_size_label):
                            assert size_of(root, widget) == size
                        assert app.font_size_label.cget("text") == str(size)
                app.font_size_var.set(15)
                app._apply_font_size()
                app.font_minus.invoke()
                assert app.font_size_var.get() == 14
                assert size_of(root, app.font_field_label) == 14
                app.font_plus.invoke()
                assert app.font_size_var.get() == 15
                app.font_size_var.set(10)
                app.font_minus.invoke()
                assert app.font_size_var.get() == 10
                app.font_size_var.set(24)
                app.font_plus.invoke()
                assert app.font_size_var.get() == 24

                app.font_size_var.set(15)
                app._apply_font_size()
                flush(root)
                scale = app.font_scale
                x, y = scale.coords()
                assert scale.identify(x, y) == "slider"
                # 点滑槽无效；拖动期间只改数值，松手才应用字体。
                scale.event_generate("<ButtonPress-1>", x=1, y=y)
                flush(root)
                assert app.font_size_var.get() == 15
                scale.event_generate("<ButtonRelease-1>", x=1, y=y)
                scale.event_generate("<ButtonPress-1>", x=x, y=y)
                target_x, target_y = scale.coords(20)
                scale.event_generate("<B1-Motion>", x=target_x, y=target_y)
                flush(root)
                moved = int(round(app.font_size_var.get()))
                assert moved == 20, moved
                assert app.font_size_label.cget("text") == "20"
                assert size_of(root, app.font_field_label) == 15
                scale.event_generate("<ButtonRelease-1>", x=target_x, y=target_y)
                flush(root)
                assert size_of(root, app.font_field_label) == 20
                # 真正按下的是蓝色滑块子控件，事件不会自动冒泡到父控件。
                app.font_size_var.set(15)
                app._apply_font_size()
                flush(root)
                thumb = scale._thumb
                delta = round(scale.coords(20)[0] - scale.coords(15)[0])
                thumb.event_generate("<ButtonPress-1>", x=7, y=12)
                thumb.event_generate("<B1-Motion>", x=7 + delta, y=12)
                flush(root)
                assert int(round(app.font_size_var.get())) == 20
                assert size_of(root, app.font_field_label) == 15
                thumb.event_generate("<ButtonRelease-1>", x=7, y=12)
                flush(root)
                assert size_of(root, app.font_field_label) == 20
                app.font_size_var.set(18)
                app._apply_font_size()
                assert app.save(silent=True)
                app.logger.remove_sink(app._on_log)
            finally:
                for callback in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(callback)
                root.destroy()
            print(f"第 {launch} 次启动：五控件边界、字号联动、按钮、拖动与配置恢复通过")
