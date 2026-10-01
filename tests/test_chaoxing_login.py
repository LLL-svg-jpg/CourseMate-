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

        # v8 登录后的门户需要点击个人空间；真实入口用新标签页打开。
        await page.context.route("https://v8.chaoxing.com/", lambda route: route.fulfill(
            body='<input id="uunnmm"><input id="pwd" type="password">'
                 '<button id="login" onclick="location.href=\'https://v1.chaoxing.com/manage\'">登录</button>',
            content_type="text/html"))
        await page.context.route("https://v1.chaoxing.com/manage", lambda route: route.fulfill(
            body='<meta charset="utf-8"><div class="login-after" '
                 'onmouseenter="document.querySelector(\'#person-space\').hidden=false">用户菜单'
                 '<ul><li id="person-space" hidden onclick="window.open(\'https://i.chaoxing.com/base\')">个人空间</li></ul></div>',
            content_type="text/html"))
        await page.context.route("https://i.chaoxing.com/base", lambda route: route.fulfill(
            body='<meta charset="utf-8"><title>个人空间</title><h1>我的课程</h1>', content_type="text/html"))
        adapter.task_url = "https://v8.chaoxing.com/"
        await asyncio.wait_for(adapter.login(page, page.context, "example-user", "example-pass"), timeout=15)
        assert page.url == "https://i.chaoxing.com/base"
        assert await page.title() == "个人空间"
        assert len(page.context.pages) == 1
        # 登录判据可能早于个人空间菜单挂载，不能 count()==0 就跳过。
        delayed = '''<meta charset="utf-8"><div class="login-after"
          onmouseenter="document.querySelector('#person-space').hidden=false">用户菜单</div>
          <script>setTimeout(()=>{
            const entry=document.createElement('li'); entry.id='person-space'; entry.hidden=true;
            entry.textContent='个人空间'; entry.onclick=()=>window.open('https://i.chaoxing.com/base');
            document.querySelector('.login-after').append(entry);
          },3000);</script>'''
        await page.context.route('https://v1.chaoxing.com/manage', lambda route: route.fulfill(
            body=delayed, content_type='text/html'))
        await asyncio.wait_for(adapter.login(page, page.context, 'example-user', 'example-pass'), timeout=15)
        assert page.url == 'https://i.chaoxing.com/base'
        assert len(page.context.pages) == 1
        # 验证通过后表单先隐藏，URL 仍短暂留在 v8，必须等门户重定向。
        await page.context.route('https://v8.chaoxing.com/', lambda route: route.fulfill(
            body='<title>跳转中</title><script>setTimeout(()=>location.href='
                 '"https://v1.chaoxing.com/manage",500);</script>', content_type='text/html'))
        await page.goto('https://v8.chaoxing.com/', wait_until='domcontentloaded')
        await adapter._enter_personal_space(page)
        assert page.url == 'https://i.chaoxing.com/base'
        assert len(page.context.pages) == 1
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("学习通登录：自动协议确认，验证码仅识别并提醒通过")
