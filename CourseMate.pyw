"""CourseMate 图形界面启动入口。

源码运行时，.pyw 后缀让 Windows 用 pythonw.exe 打开，双击不弹黑框。
打包成 exe 后，本文件是 PyInstaller 的入口脚本。

这里的每一处异常都必须落盘并弹窗：打包后的 --windowed 程序没有控制台，
崩溃信息会被彻底吞掉，用户只会看到"双击没反应"。
"""
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _app_dir() -> Path:
    """程序数据目录。打包后是 exe 所在目录，源码运行时是脚本目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _app_dir()
# 双击运行时工作目录未必是程序所在目录，配置和日志会写错地方
os.chdir(ROOT)
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(ROOT))


def _write_crash(text: str) -> Path | None:
    """把崩溃详情写到文件。这是 --windowed 下唯一能留下的线索。"""
    try:
        crash_dir = ROOT / "runtime"
        crash_dir.mkdir(parents=True, exist_ok=True)
        path = crash_dir / f"crash_{datetime.now():%Y%m%d_%H%M%S}.log"
        path.write_text(text, encoding="utf-8")
        return path
    except Exception:
        return None


def _fatal(title: str, message: str, detail: str = "") -> None:
    """启动阶段的错误必须用弹窗报告——没有控制台，print 会石沉大海。"""
    saved = _write_crash(f"{title}\n\n{message}\n\n{detail}") if detail else None
    if saved:
        message += f"\n\n详细信息已保存到：\n{saved}"
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        # 连 tkinter 都起不来时，至少别静默失败
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        except Exception:
            pass


def main() -> int:
    if sys.version_info < (3, 11):
        _fatal(
            "Python 版本过低",
            f"CourseMate 需要 Python 3.11 或更高版本。\n"
            f"当前版本：{sys.version.split()[0]}",
        )
        return 1

    try:
        from coursemate.gui import main as gui_main
    except ImportError as exc:
        _fatal(
            "启动失败",
            f"无法载入界面模块：{exc}\n\n"
            + ("这是打包版本，说明缺少某个依赖模块。"
               if getattr(sys, "frozen", False)
               else f"请确认本文件与 coursemate 文件夹在同一目录下。\n当前目录：{ROOT}"),
            traceback.format_exc(),
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        _fatal("启动失败", f"发生未预期的错误：\n\n{exc!r}", traceback.format_exc())
        return 1

    try:
        return gui_main()
    except Exception as exc:  # noqa: BLE001
        _fatal("程序异常退出", f"运行中出现未处理的错误：\n\n{exc!r}",
               traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
