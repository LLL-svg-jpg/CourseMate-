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
<label class="agreement"><input type="checkbox">同意用户协议</label>
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
        await asyncio.wait_for(
            adapter.login(page, page.context, "example-user", "example-pass"), timeout=20
        )
        assert "accepted=true" in page.url
        assert await adapter.is_logged_in(page)

        await page.set_content('<div class="yidun_modal__title">请完成安全验证</div>')
        assert await adapter.detect_captcha(page)
        await page.locator(".yidun_modal__title").evaluate("node => node.style.display = 'none'")
        assert not await adapter.detect_captcha(page)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧树登录：自动协议确认，验证码仅识别并提醒通过")
