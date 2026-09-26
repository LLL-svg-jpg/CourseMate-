"""智慧职教账号登录及登录后绑定提示。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.icve import IcveAdapter


HTML = """<!doctype html><meta charset="utf-8">
<span id="tab" onclick="document.querySelector('#form').hidden=false">账号密码登录</span>
<form id="form" class="demo-ruleForm" hidden onsubmit="return false">
  <input placeholder="请输入账号">
  <input placeholder="请输入密码" type="password">
  <label><input type="checkbox">我已阅读并同意</label>
  <div id="login" class="login" onclick="if(document.querySelector('input[type=checkbox]').checked)
    document.querySelector('#binding').hidden=false">登录</div>
</form>
<div id="binding" hidden>
  <button id="never" onclick="document.body.dataset.never='1'">不再提醒</button>
  <button id="later" onclick="document.querySelector('#binding').hidden=true;
    history.pushState({}, '', '/study/v2/index');
    document.body.insertAdjacentHTML('beforeend', '<h5>我的课程</h5>')">下次绑定</button>
</div>"""


async def run() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="msedge")
        page = await browser.new_page()
        await page.route(IcveAdapter.login_url, lambda route: route.fulfill(body=HTML, content_type="text/html"))
        adapter = IcveAdapter()
        await asyncio.wait_for(adapter.login(page, page.context, "example-user", "example-pass"), timeout=20)
        assert await page.get_by_placeholder("请输入账号").input_value() == "example-user"
        assert await page.get_by_placeholder("请输入密码").input_value() == "example-pass"
        assert await page.get_by_role("checkbox").is_checked()
        assert await page.locator("#binding").is_hidden()
        assert await page.locator("body").get_attribute("data-never") is None
        assert page.url.endswith("/study/v2/index")
        assert await adapter.is_logged_in(page)
        await page.route("https://sso.icve.com.cn/check", lambda route: route.fulfill(
            body='<meta charset="utf-8"><div>请完成安全验证</div>', content_type="text/html"))
        await page.goto("https://sso.icve.com.cn/check")
        assert await adapter.detect_captcha(page)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧职教登录：账号页、自动协议、提交、下次绑定及新版主页识别通过")
