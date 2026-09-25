"""等待人工登录时，停止按钮必须取消登录任务。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.runner import StopRequested, ensure_login


class WaitingLogin:
    cancelled = False

    async def is_logged_in(self, _page) -> bool:
        return False

    async def login(self, _page, _context, _username, _password) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


async def run() -> None:
    adapter = WaitingLogin()
    stop = asyncio.Event()
    config = SimpleNamespace(username="", password="")
    task = asyncio.create_task(ensure_login(None, None, adapter, config, stop.is_set))
    await asyncio.sleep(0.1)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=2)
    except StopRequested:
        pass
    else:
        raise AssertionError("停止指令没有中断登录等待")
    assert adapter.cancelled


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧职教登录等待收到停止指令后及时退出")
