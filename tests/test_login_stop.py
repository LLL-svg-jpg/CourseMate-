"""等待人工登录时，停止按钮必须取消登录任务。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.runner import StopRequested, ensure_login


class WaitingLogin:
    cancelled = False
    name = "测试平台"

    async def is_logged_in(self, _page) -> bool:
        return False

    async def login(self, _page, _context, _username, _password) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True

    async def detect_captcha(self, _page) -> bool:
        return True


async def run() -> None:
    adapter = WaitingLogin()
    stop = asyncio.Event()
    config = SimpleNamespace(username="", password="")
    messages = []
    with patch("coursemate.runner.logger.warn", side_effect=lambda message, **_: messages.append(message)):
        task = asyncio.create_task(ensure_login(None, None, adapter, config, stop.is_set))
        await asyncio.sleep(0.7)
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2)
        except StopRequested:
            pass
        else:
            raise AssertionError("停止指令没有中断登录等待")
    assert adapter.cancelled
    assert any("登录页出现安全验证" in message for message in messages)
    assert all("[需要你处理]" not in message for message in messages)


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧职教登录等待收到停止指令后及时退出")
