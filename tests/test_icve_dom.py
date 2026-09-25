"""智慧职教目录按课件 ID 读取，已完成视频和 PPT 不重复学习。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.icve import IcveAdapter


HTML = """<!doctype html><meta charset="utf-8">
<div class="coursePreviewIndex"><div class="courseDataTree"><div class="list">
  <div class="listItem"><div class="items"><div class="ts" onclick="show(this)">第一章</div></div>
    <div hidden><div class="items iChild"><div class="ts" onclick="show(this)">第一节</div></div>
      <div class="fList" hidden>
        <div class="fwi"><div class="name">完成视频</div></div>
        <div class="fwi"><div class="name">未完成视频</div></div>
        <div class="fwi"><div class="name">演示文稿</div></div>
        <div class="fwi"><div class="name">PDF 文档</div></div>
      </div>
    </div>
  </div>
</div></div></div>
<script>
function show(el) { el.parentElement.nextElementSibling.hidden = false; }
document.querySelector('.coursePreviewIndex').__vue__ = {list:[{children:[{children:[
  {id:'done',name:'完成视频',fileType:'video',speed:100},
  {id:'todo',name:'未完成视频',fileType:'video',speed:0},
  {id:'slides',name:'演示文稿',fileType:'ppt',speed:0},
  {id:'pdf',name:'PDF 文档',fileType:'pdf',speed:0},
]}]}]};
</script>"""

DELAYED_HTML = """<!doctype html><meta charset="utf-8">
<div class="coursePreviewIndex"><div><div><div class="listItem">
  <div class="items"><div class="ts" onclick="openChapter()">第一章</div></div>
  <div id="groups" hidden><div class="items iChild"><div class="ts" onclick="show(this)">第一节</div></div>
    <div class="fList" hidden><div class="fwi">第一视频</div></div></div>
</div></div></div></div>
<script>
const vue = document.querySelector('.coursePreviewIndex').__vue__ =
  {list:[{children:[{children:[{id:'one',name:'第一视频',fileType:'video',speed:0}]}]}]};
function show(el) { el.parentElement.nextElementSibling.hidden = false; }
function openChapter() {
  document.querySelector('#groups').hidden = false;
  fetch('/groups').then(() => {
    vue.list[0].children.push({children:[{id:'two',name:'第二视频',fileType:'video',speed:0}]});
    document.querySelector('.listItem').insertAdjacentHTML('beforeend',
      '<div><div class="items iChild"><div class="ts" onclick="show(this)">第二节</div></div>' +
      '<div class="fList" hidden><div class="fwi">第二视频</div></div></div>');
  });
}
</script>"""


async def run() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="msedge")
        page = await browser.new_page()
        await page.set_content(HTML)
        adapter = IcveAdapter()
        adapter._start_id = "done"

        async def stay(_page):
            return None

        adapter._open_index = stay
        lessons = await adapter.list_lessons(page)
        assert [x.key for x in lessons] == ["done", "todo", "slides", "pdf"]
        assert [x.finished for x in lessons] == [True, False, False, False]
        assert [x.kind for x in lessons] == ["video", "video", "ppt", "pdf"]
        assert adapter._paths["todo"] == (0, 0, 1)
        assert adapter._start_id == "todo"

        async def serve(route):
            if route.request.url.endswith("/groups"):
                await asyncio.sleep(0.3)
                await route.fulfill(body="ok")
            else:
                await route.fulfill(body=DELAYED_HTML, content_type="text/html")

        await page.route("https://course.test/**", serve)
        await page.goto("https://course.test/")
        adapter = IcveAdapter()
        adapter._open_index = stay
        delayed = await adapter.list_lessons(page)
        assert [x.key for x in delayed] == ["one", "two"]

        full = [dict(id=key, name=key, type="video", speed=0, path=[0, 0, i])
                for i, key in enumerate(("one", "two"))]
        adapter._read_catalog = AsyncMock(side_effect=[full[:1], full])
        restored = await adapter.list_lessons(page)
        assert [x.key for x in restored] == ["one", "two"]
        adapter._read_catalog = AsyncMock(side_effect=[full[:1], full[:1]])
        try:
            await adapter.list_lessons(page)
        except RuntimeError as exc:
            assert "少了 1 节" in str(exc)
        else:
            raise AssertionError("目录缩水两次后不应被当成完整课程")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧职教目录 DOM：视频、PPT 和 PDF 的顺序、课件 ID 及完成状态通过")
