"""暂停协调与学习计时。

核心问题：人机验证、答题都会打断播放。如果把这些时间算进"已学习时长"，
limit_max_minutes 会被非学习时间吃掉，用户以为刷了 60 分钟其实只播了 20 分钟。

解法（借鉴 Autovisor）：中断开始时记时间戳，恢复时把这段差值累加到 paused，
有效时长 = 墙钟时间 - 累计暂停时间。
"""
from __future__ import annotations

import asyncio
import time


class Interruption:
    """一类中断（如人机验证）的信号量。"""

    def __init__(self, name: str):
        self.name = name
        self._event = asyncio.Event()

    def resolve(self) -> None:
        """中断已处理完毕，唤醒等待者。"""
        self._event.set()

    async def wait(self) -> float:
        """阻塞直到中断解除，返回本次阻塞秒数。"""
        self._event.clear()
        start = time.time()
        await self._event.wait()
        return time.time() - start


class StudyClock:
    """有效学习时长计时器。"""

    def __init__(self) -> None:
        self._start = time.time()
        self._paused = 0.0

    def reset(self) -> None:
        self._start = time.time()
        self._paused = 0.0

    def add_paused(self, seconds: float) -> None:
        self._paused += max(0.0, seconds)

    @property
    def elapsed_minutes(self) -> float:
        return max(0.0, time.time() - self._start - self._paused) / 60

    @property
    def paused_minutes(self) -> float:
        return self._paused / 60

    def reached(self, limit_minutes: float) -> bool:
        """limit_minutes <= 0 表示不限时。"""
        return 0 < limit_minutes <= self.elapsed_minutes
