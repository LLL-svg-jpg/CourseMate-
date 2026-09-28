"""智慧树登录自动勾选明确协议框；验证码只检测，不自动处理。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.zhihuishu import ZhihuishuAdapter


LOGIN_HTML = """<!doctype html><meta charset="utf-8">
<input name="mobile"><input type="password">
<label class="el-checkbox privacy-checkbox">
  <span class="el-checkbox__input" style="display:inline-block;width:12px;height:12px">
    <input type="checkbox" style="width:0;height:0">
    <span class="el-checkbox__inner" style="display:inline-block;width:12px;height:12px"></span>
  </span>
  我已阅读并同意<span class="privacy-con" onclick="console.log('protocol-clicked')">用户协议</span>
</label>
<button class="btn-block__grandient_login" onclick="location.href=
'https://studyh5.zhihuishu.com/course?accepted=' + document.querySelector('input[type=checkbox]').checked">登录</button>"""


async def run() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="msedge")
        page = await browser.new_page()
        await page.route(
            "https://login.zhihuishu.com/**",
            lambda route: route.fulfill(body=LOGIN_HTML, content_type="text/html"),
        )
        await page.route(
            "https://studyh5.zhihuishu.com/**",
            lambda route: route.fulfill(body="<title>学习页面</title>", content_type="text/html"),
        )
        adapter = ZhihuishuAdapter()
        protocol_events = []
        page.on("console", lambda message: protocol_events.append(message.text))
        await asyncio.wait_for(
            adapter.login(page, page.context, "example-user", "example-pass"), timeout=20
        )
        assert "accepted=true" in page.url
        assert await adapter.is_logged_in(page)
        assert "protocol-clicked" not in protocol_events

        manual = await browser.new_page()
        await manual.route(
            "https://login.zhihuishu.com/**",
            lambda route: route.fulfill(body=LOGIN_HTML, content_type="text/html"),
        )
        await manual.route(
            "https://studyh5.zhihuishu.com/**",
            lambda route: route.fulfill(body="<title>学习页面</title>", content_type="text/html"),
        )
        manual.on("console", lambda message: protocol_events.append(message.text))
        login_task = asyncio.create_task(adapter.login(manual, manual.context, "", ""))
        await manual.locator(".privacy-checkbox input").wait_for(state="attached")
        for _ in range(30):
            if await manual.locator(".privacy-checkbox input").is_checked():
                break
            await asyncio.sleep(0.1)
        assert await manual.locator(".privacy-checkbox input").is_checked()
        await manual.locator(".btn-block__grandient_login").click()
        await asyncio.wait_for(login_task, timeout=5)
        assert "accepted=true" in manual.url
        assert "protocol-clicked" not in protocol_events

        await page.set_content('<div class="yidun_modal__title">请完成安全验证</div>')
        assert await adapter.detect_captcha(page)
        await page.locator(".yidun_modal__title").evaluate("node => node.style.display = 'none'")
        assert not await adapter.detect_captcha(page)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧树登录：自动协议确认，验证码仅识别并提醒通过")
