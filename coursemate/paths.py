"""路径解析：区分"程序自带的资源"和"用户的数据"。

打包成 exe 后这两者会分家，必须分别对待：

- 资源（图标等）被 PyInstaller 解压到临时目录 sys._MEIPASS，
  每次运行路径都不同，程序结束就删。
- 用户数据（config.toml、Cookie、题库、日志）必须留在 exe 旁边，
  否则用户改完配置一重启就没了，日志也永远找不到。

源码运行时两者都是项目根目录，所以平时看不出区别——
正因为看不出，才容易在打包后才炸，这里一次性理清。
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的 exe 里。"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_dir() -> Path:
    """只读资源目录（图标等随程序分发的东西）。"""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """用户数据目录：配置、Cookie、题库、日志都写这里。

    打包后是 exe 所在的文件夹，源码运行时是项目根目录。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def user_file(*parts: str) -> Path:
    return app_dir().joinpath(*parts)
