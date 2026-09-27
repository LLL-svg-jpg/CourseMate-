"""Windows 首次启动须先设置 DPI，再创建 Tk 窗口。"""
import ctypes
import sys
from types import SimpleNamespace
from unittest.mock import patch

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate import gui


if sys.platform != "win32":
    print("非 Windows 环境，跳过 DPI 启动顺序测试")
else:
    events = []

    class Root:
        def __init__(self):
            events.append("tk")
            self.tk = SimpleNamespace(call=lambda *args: events.append(("scaling", args)))

        def mainloop(self):
            events.append("loop")

    windll = SimpleNamespace(
        shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=lambda _:
                                events.append("appid")),
        shcore=SimpleNamespace(SetProcessDpiAwareness=lambda _:
                               events.append("dpi")),
    )
    with patch.object(ctypes, "windll", windll), patch.object(gui.tk, "Tk", Root), \
            patch.object(gui, "CourseMateGUI", lambda _root: events.append("gui")):
        assert gui.main() == 0

    assert events[0:3] == ["appid", "dpi", "tk"], events
    assert events[3][0] == "scaling" and events[3][1][:2] == ("tk", "scaling")
    assert abs(events[3][1][2] - 4 / 3) < 0.001
    assert events[4:] == ["gui", "loop"], events
    print("DPI、窗口、字号和界面构建顺序：通过")
