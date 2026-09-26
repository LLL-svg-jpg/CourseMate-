"""学习通登录自动勾选明确协议框；验证码只检测，不自动处理。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.chaoxing import ChaoxingAdapter


LOGIN_HTML = """<!doctype html><meta charset="utf-8">
<input id="phone"><input id="pwd" type="password">
<label id="passportAgreement"><input type="checkbox">我已阅读用户协议</label>
<button id="loginBtn" onclick="if (document.querySelector('input[type=checkbox]').checked)
  location.href='https://mooc1.chaoxing.com/home?accepted=' + document.querySelector('input[type=checkbox]').checked">登录</button>"""


async def run() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="msedge")
        page = await browser.new_page()
        await page.route(
            ChaoxingAdapter.login_url,
            lambda route: route.fulfill(body=LOGIN_HTML, content_type="text/html"),
        )
        await page.route(
            "https://mooc1.chaoxing.com/**",
            lambda route: route.fulfill(body="<title>学习空间</title>", content_type="text/html"),
        )
        adapter = ChaoxingAdapter()
        login_task = asyncio.create_task(
            adapter.login(page, page.context, "example-user", "example-pass")
        )
        await page.wait_for_function(
            "document.querySelector('#phone').value === 'example-user' && "
            "document.querySelector('#pwd').value === 'example-pass'"
        )
        assert await page.locator("#pwd").input_value() == "example-pass"
        assert await page.locator('#passportAgreement input[type="checkbox"]').count() == 1
        assert await page.locator('#passportAgreement input[type="checkbox"]').is_visible()
        await page.wait_for_url("**accepted=true", timeout=10000)
        await asyncio.wait_for(login_task, timeout=8)
        assert "accepted=true" in page.url
        assert await adapter.is_logged_in(page)

        await page.set_content(
            '<input id="verifyCode"><img id="captchaImage" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">'
        )
        assert await adapter.detect_captcha(page)
        await page.locator("#verifyCode").evaluate("node => node.style.display = 'none'")
        await page.locator("#captchaImage").evaluate("node => node.style.display = 'none'")
        assert not await adapter.detect_captcha(page)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("学习通登录：自动协议确认，验证码仅识别并提醒通过")
