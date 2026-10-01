"""课程首页目录入口回归；页面与跳转均由本地路由提供。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.chaoxing import ChaoxingAdapter
from coursemate.platforms.icve import IcveAdapter
from coursemate.platforms.zhihuishu import ZhihuishuAdapter


async def run() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="msedge")
        page = await browser.new_page()
        pages = {
            "/home": '<iframe src="/catalog"></iframe>',
            "/catalog": """<div class="chapter_item">第一章</div>
                <div class="chapter_item" id="cur1" onclick="top.location.href='/player'">
                  <span class="catalog_name">第一节</span><i class="icon_yiwanc"></i>
                  <input class="knowledgeJobCount" value="1">
                </div>
                <div class="chapter_item" id="cur2">
                  <span class="catalog_name">第二节</span><input class="knowledgeJobCount" value="0">
                </div>""",
            "/player": """<div class="posCatalog_select posCatalog_active" id="cur1">
                  <span class="posCatalog_name">第一节</span><input class="jobUnfinishCount" value="1">
                </div><div class="posCatalog_select" id="cur2">
                  <span class="posCatalog_name">第二节</span><i class="icon_Completed"></i>
                </div><video></video>""",
        }

        async def serve(route):
            from urllib.parse import urlparse
            await route.fulfill(body=pages[urlparse(route.request.url).path], content_type="text/html; charset=utf-8")

        await page.route("https://course.test/**", serve)
        await page.goto("https://course.test/home")
        adapter = ChaoxingAdapter()
        lessons = await adapter.list_lessons(page)
        assert [item.key for item in lessons] == ["cur1", "cur2"]
        assert [item.title for item in lessons] == ["第一节", "第二节"]
        assert [item.finished for item in lessons] == [False, True]
        assert await adapter.enter_lesson(page, lessons[0])
        assert page.url == "https://course.test/player"
        refreshed = await adapter.list_lessons(page)
        assert [item.key for item in refreshed] == ["cur1", "cur2"]
        assert await adapter.active_lesson_key(page) == "cur1"
        await page.locator("#cur2").evaluate("el => el.outerHTML=el.outerHTML.replace('第二节','重绘第二节')")
        assert (await refreshed[1].handle.text_content()).strip() == "重绘第二节"

        async def icve_serve(route):
            if "/spoccourseIndex" in route.request.url:
                body = '<div class="listItem">第一章</div>'
            else:
                body = """<meta charset="utf-8"><script>setTimeout(() => {
                    document.body.innerHTML = '<div class="navItem"><span class="text">我的课程</span></div>' +
                      '<div class="el-tabs__item">标准课程</div><div class="el-tabs__item">快速课程</div>' +
                      '<div class="course"><div class="case"><button>查看</button></div></div>';
                    document.querySelector('button').onclick = () => location.href='/study/coursePreview/spoccourseIndex';
                    document.querySelector('.course').__vue__ = {caselist:[{classId:'local-class',courseName:'本地课程'}]};
                }, 200);</script>"""
            await route.fulfill(body=body, content_type="text/html; charset=utf-8")

        await page.route("https://zjy2.icve.com.cn/**", icve_serve)
        icve = IcveAdapter()
        title = await icve.open_course(page, icve.index_url + "?classId=local-class")
        assert title == "本地课程", title
        assert page.url == icve.index_url

        await page.set_content("""<meta charset="utf-8">
            <ul class="list"><li class="video" onclick="this.classList.add('current_play')">第一视频</li></ul>
            <video></video>
            <div class="dialog" style="display:none;position:fixed;inset:0;background:#fff">
              <div class="dialog-read"><button class="iconguanbi" onclick="this.closest('.dialog').remove()">关闭</button></div>
            </div>""")
        zhs = ZhihuishuAdapter()
        zhs.is_shared = True
        await zhs.prepare_page(page)
        lessons = await zhs.list_lessons(page)
        await page.locator(".dialog").evaluate("el => el.style.display='block'")
        assert await zhs.enter_lesson(page, lessons[0])
        assert await page.locator(".dialog").count() == 0
        await browser.close()
    print("[PASS] 学习通首页 iframe 目录、完成判定、原生跳转及播放页目录重读")
    print("[PASS] 智慧职教课程列表延迟挂载后按班级进入课程首页")
    print("[PASS] 智慧树目录加载后出现学前必读弹窗，进入小节前关闭")


if __name__ == "__main__":
    asyncio.run(run())
