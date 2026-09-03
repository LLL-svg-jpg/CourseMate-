"""彩色控制台 + 文件日志。

陪伴程序会长时间无人值守运行，日志是唯一的现场记录，
所以异常一律落盘，控制台只展示人看得懂的进度。
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}

_COLORS = {
    "DEBUG": "\033[90m",
    "INFO": "\033[36m",
    "WARN": "\033[33m",
    "ERROR": "\033[31m",
}
_RESET = "\033[0m"


class Logger:
    """进程内共享的单例日志器。"""

    _instance: "Logger | None" = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._ready = False
        return cls._instance

    def __init__(self, level: str = "INFO", log_dir: Path | None = None):
        if self._ready:
            return
        self._ready = True
        self.level = _LEVELS.get(level.upper(), 20)
        if log_dir is None:
            from .paths import app_dir

            log_dir = app_dir() / "runtime" / "logs"
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"coursemate_{stamp}.log"
        self._buffer: list[str] = []
        # 订阅者。GUI 注册回调后即可实时收到日志，控制台输出不受影响。
        # 回调在产生日志的线程里被调用，GUI 端必须自己做线程转交。
        self._sinks: list = []
        # Windows 终端默认不解析 ANSI 转义，显式开启
        if sys.platform == "win32":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except Exception:
                pass

    def set_level(self, level: str) -> None:
        self.level = _LEVELS.get(level.upper(), 20)

    def add_sink(self, callback) -> None:
        """注册日志订阅者，callback(level, message, timestamp)。"""
        if callback not in self._sinks:
            self._sinks.append(callback)

    def remove_sink(self, callback) -> None:
        if callback in self._sinks:
            self._sinks.remove(callback)

    def _emit(self, level: str, msg: str, shift: bool = False) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level:<5}] {msg}"
        self._buffer.append(line)
        if _LEVELS[level] >= self.level:
            prefix = "\n" if shift else ""
            color = _COLORS.get(level, "")
            try:
                print(f"{prefix}{color}{line}{_RESET}", flush=True)
            except (OSError, ValueError):
                # pythonw 启动时没有真正的 stdout，写入会失败——不能因此中断流程
                pass
            for sink in list(self._sinks):
                try:
                    sink(level, msg, ts)
                except Exception:
                    # 订阅者自己的异常绝不能影响主流程
                    pass

    def debug(self, msg: str, shift: bool = False) -> None:
        self._emit("DEBUG", msg, shift)

    def info(self, msg: str, shift: bool = False) -> None:
        self._emit("INFO", msg, shift)

    def warn(self, msg: str, shift: bool = False) -> None:
        self._emit("WARN", msg, shift)

    def error(self, msg: str, shift: bool = False) -> None:
        self._emit("ERROR", msg, shift)

    @staticmethod
    def summarize(exc: BaseException) -> str:
        """把异常压成一行，用于高频轮询场景，避免刷屏。"""
        text = str(exc).strip().splitlines()
        head = text[0] if text else exc.__class__.__name__
        return f"{exc.__class__.__name__}: {head[:160]}"

    def log_exception(self, msg: str, exc: BaseException, shift: bool = False) -> None:
        self.error(f"{msg} ({self.summarize(exc)})", shift)
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._buffer.append(detail)

    def progress(self, desc: str, value: str) -> None:
        """原地刷新的单行进度，不写入日志文件，避免刷爆磁盘。"""
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            sys.stdout.write(f"\r[{ts}] [INFO ] {desc} {value}   ")
            sys.stdout.flush()
        except (OSError, ValueError, AttributeError):
            # pythonw 下无 stdout
            pass
        for sink in list(self._sinks):
            try:
                sink("PROGRESS", f"{desc} {value}", ts)
            except Exception:
                pass

    def save(self) -> None:
        try:
            self.log_file.write_text("\n".join(self._buffer), encoding="utf-8")
        except OSError:
            pass
