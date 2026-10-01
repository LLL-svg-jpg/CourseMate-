"""字号响应、AI 初始地址、关于页链接和页签回归；不调用模型或真实平台。"""
from pathlib import Path
import ctypes
import sys
import tempfile
import tkinter as tk
from tkinter import font, ttk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate import providers
from coursemate.config_writer import save_config
from coursemate.gui import CourseMateGUI


if sys.platform == "win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(1)


with tempfile.TemporaryDirectory() as temp:
    data = Path(temp)
    config = data / "config.toml"
    with patch("coursemate.gui.CONFIG_PATH", config), \
         patch("coursemate.gui.app_dir", lambda: data), \
         patch.object(CourseMateGUI, "_start_tray"):
        for case in ("missing", "invalid", "custom"):
            if case == "invalid":
                config.write_text("invalid = [", encoding="utf-8")
            elif case == "custom":
                save_config(config, {"provider": "deepseek", "answer_enabled": False,
                                     "base_url": "https://example.invalid/custom/v1"})
            root = tk.Tk()
            try:
                root.tk.call("tk", "scaling", 4 / 3)
                app = CourseMateGUI(root)
                expected = ("https://example.invalid/custom/v1" if case == "custom"
                            else providers.get("deepseek").base_url)
                assert app.vendor_key() == "deepseek"
                assert app.base_url_var.get() == expected
                assert app.base_url_entry.get() == expected
                root.update()
                if case != "missing":
                    print(f"{case}：启动地址正确")
                    continue

                notebook = next(w for w in app.body.winfo_children() if isinstance(w, ttk.Notebook))
                notebook.select(2)
                notebook.focus_force()
                root.update()
                assert "Notebook.focus" not in str(ttk.Style(root).layout("TNotebook.Tab"))
                for key, expected_tab in (("Left", 1), ("Left", 0), ("Right", 1), ("Right", 2)):
                    notebook.event_generate(f"<{key}>")
                    root.update()
                    assert notebook.index(notebook.select()) == expected_tab
                for button in (app.font_minus, app.font_plus):
                    assert button.winfo_reqwidth() <= 60
                    assert button.winfo_reqheight() <= 48
                assert app.font_minus.winfo_reqwidth() == app.font_plus.winfo_reqwidth()
                assert app.font_minus.winfo_reqheight() == app.font_plus.winfo_reqheight()

                # 连点先显示选择值，不能在按钮回调里同步重绘整窗。
                with patch.object(app, "_apply_font_size", wraps=app._apply_font_size) as apply, \
                     patch.object(root, "update_idletasks", wraps=root.update_idletasks) as redraw:
                    original_jobs = set(root.tk.splitlist(root.tk.call("after", "info")))
                    for delta in (1, 1, -1, 1):
                        app._nudge_font(delta)
                        size = int(app.font_size_var.get())
                        assert app.font_size_label.cget("text") == str(size)
                        assert font.Font(root=root, font=app.font_field_label.cget("font")).actual("size") == size
                    assert apply.call_count == 0 and redraw.call_count == 0
                    jobs = set(root.tk.splitlist(root.tk.call("after", "info")))
                    assert jobs - original_jobs == {app._font_apply_job}
                    root.after(600, root.quit)
                    root.mainloop()
                    assert apply.call_count == 1
                    assert app._font_apply_job is None
                    assert font.Font(root=root, font=app.base_url_entry.cget("font")).actual("size") == 17
                    wanted = max(700, 660 + 17 * 9, root.winfo_reqheight())
                    cap = max(700, root.winfo_screenheight() - 90)
                    assert root.minsize() == (1000, min(wanted, cap))

                # 直接应用（含滑块松手）必须取消旧的延迟任务，保存采用当前选择值。
                app.font_plus.invoke()
                pending = app._font_apply_job
                assert app._collect()["font_size"] == 18
                app.font_size_var.set(20)
                app._apply_font_size()
                assert pending not in root.tk.splitlist(root.tk.call("after", "info"))
                assert app._font_apply_job is None
                assert font.Font(root=root, font=app.base_url_entry.cget("font")).actual("size") == 20
                assert "Notebook.focus" not in str(ttk.Style(root).layout("TNotebook.Tab"))

                # 关于页使用本机可用浏览器打开项目，继承全局字号。
                app._show_section("关于")
                root.update()
                assert str(app.project_link.cget("cursor")) == "hand2"
                assert font.Font(root=root, font=ttk.Style(root).lookup("TLabel", "font")).actual("size") == 20
                assert app.project_link.winfo_x() + app.project_link.winfo_width() <= app.project_link.master.winfo_width()
                with patch("coursemate.config.detect_browser", return_value=("msedge", "example-browser.exe")), \
                     patch("coursemate.gui.subprocess.Popen") as open_browser:
                    app.project_link.event_generate("<Button-1>")
                    app.project_link.focus_force()
                    root.update()
                    for event in ("<Return>", "<space>"):
                        app.project_link.event_generate(event)
                    assert open_browser.call_count == 3
                    for call in open_browser.call_args_list:
                        assert call.args == (["example-browser.exe", "https://github.com/LLL-svg-jpg/CourseMate-"],)
                with patch("coursemate.config.detect_browser", return_value=("chromium", None)), \
                     patch("coursemate.gui.webbrowser.open", return_value=True) as open_browser:
                    app.project_link.event_generate("<Button-1>")
                    open_browser.assert_called_once_with("https://github.com/LLL-svg-jpg/CourseMate-")
            finally:
                app.logger.remove_sink(app._on_log)
                for callback in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(callback)
                root.destroy()
            print(f"{case}：初始地址、字号连点合并、按钮尺寸通过")

print("默认/损坏配置地址、自定义地址保留、连点与松手取消、关于页链接、页签键盘切换：通过")
