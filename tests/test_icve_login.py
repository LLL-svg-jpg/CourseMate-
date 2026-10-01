"""智慧职教账号登录及登录后绑定提示。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.icve import IcveAdapter
from coursemate.platforms import resolve


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

SSO_URL = "https://sso.icve.com.cn/sso/auth"
SSO_HTML = """<!doctype html><meta charset="utf-8">
<span onclick="document.querySelector('#form').hidden=false">账号密码登录</span>
<form id="form" class="demo-ruleForm" hidden onsubmit="return false">
  <input placeholder="请输入账号" type="text"><input placeholder="请输入密码" type="password">
  <div class="agreement"><label class="el-checkbox">
    <input type="checkbox" style="width:0;height:0">
    <span class="el-checkbox__inner" style="display:inline-block;width:16px;height:16px"></span>
  </label><a onclick="document.body.dataset.protocol='1'">隐私协议</a></div>
  <div class="login" onclick="if(document.querySelector('input[type=checkbox]').checked)
    location.href='https://zjy2.icve.com.cn/study/course?filled=' +
    (document.querySelector('input[type=text]').value === 'example-user' &amp;&amp;
     document.querySelector('input[type=password]').value === 'example-pass') +
    '&amp;protocol=' + (document.body.dataset.protocol || '0')">登录</div>
</form>"""


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

        # 登录页会同时保留一个隐藏的同名按钮；必须点击当前可见的绑定提示。
        await page.set_content('<button hidden>下次绑定</button><button '
            'onclick="document.body.dataset.later=1">下次绑定</button>'
            '<button onclick="document.body.dataset.never=1">不再提醒</button>')
        await adapter._dismiss_wechat_binding(page)
        assert await page.locator('body').get_attribute('data-later') == '1'
        assert await page.locator('body').get_attribute('data-never') is None

        # SSO 入口必须能路由并保留；原生框隐藏时点自绘框，不能打开协议链接。
        assert isinstance(resolve(SSO_URL), IcveAdapter)
        assert resolve("https://sso.icve.com.cn/unrelated") is None
        await page.route(SSO_URL, lambda route: route.fulfill(body=SSO_HTML, content_type="text/html"))
        await page.route("https://zjy2.icve.com.cn/study/course**", lambda route: route.fulfill(
            body='<meta charset="utf-8"><h5>我的课程</h5>', content_type="text/html"))
        adapter = resolve(SSO_URL)
        adapter.task_url = SSO_URL
        await asyncio.wait_for(adapter.login(page, page.context, "example-user", "example-pass"), timeout=15)
        assert "filled=true&protocol=0" in page.url
        assert await adapter.is_logged_in(page)

        # 新 SSO 个人后台必须同时有会话凭证及可见用户界面；仅 URL 不算登录。
        await page.route('https://sso.icve.com.cn/sso/backstage', lambda route: route.fulfill(
            body='<div class="avatar-wrapper"><img class="user-avatar" '
                 'style="width:20px;height:20px"></div><form class="el-form info">个人资料</form>',
            content_type='text/html'))
        await page.goto('https://sso.icve.com.cn/sso/backstage')
        assert not await adapter.is_logged_in(page)
        await page.context.add_cookies([{'name':'token','value':'example-session',
                                       'url':'https://sso.icve.com.cn/'}])
        assert await adapter.is_logged_in(page)
        await page.goto(SSO_URL)
        assert not await adapter.is_logged_in(page)

        # 阿里云 SDK 的初始化可晚于登录表单；登录按钮须等图片加载完成再点。
        waiting = SSO_HTML.replace('if(document.querySelector', 'if(window.ready &amp;&amp; document.querySelector') + '''
          <img id="aliyunCaptcha-img" hidden>
          <script>
            window.initAliyunCaptcha=()=>{}; window.ready=false;
            setTimeout(()=>{
              const img=document.querySelector('#aliyunCaptcha-img');
              img.onload=()=>window.ready=true;
              img.src='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
            },2000);
          </script>'''
        await page.route(SSO_URL, lambda route: route.fulfill(body=waiting, content_type='text/html'))
        await asyncio.wait_for(adapter.login(page, page.context, 'example-user', 'example-pass'), timeout=8)
        assert 'filled=true&protocol=0' in page.url
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧职教登录：账号页、自动协议、提交、下次绑定及新版主页识别通过")
