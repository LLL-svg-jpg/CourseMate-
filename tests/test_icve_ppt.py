"""智慧职教 PPT 必须从首页逐页读到末页，且可响应停止。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.base import Lesson
from coursemate.platforms.icve import IcveAdapter
from coursemate.runner import StopRequested, study_lesson


HTML = """<meta charset="utf-8"><div class="FilePreview"><div class="page">
<button onclick="step(-1)">上一页</button><span id="count">2 / 4</span>
<button onclick="step(1)">下一页</button></div></div>
<script>
let current = 2;
window.visited = [2];
function step(delta) {
  current = Math.max(1, Math.min(4, current + delta));
  document.querySelector('#count').textContent = `${current} / 4`;
  window.visited.push(current);
}
</script>"""

PDF_HTML = """<meta charset="utf-8"><main><div id="viewer">PDF 预览</div>
<div><button id="previous" onclick="step(-1)">上一页</button>
<span id="pdf-count">3 / 4</span>
<button id="next" onclick="step(1)">下一页</button></div></main>
<script>
let current = 3;
window.visited = [3];
function step(delta) {
  current = Math.max(1, Math.min(4, current + delta));
  document.querySelector('#pdf-count').textContent = `${current} / 4`;
  window.visited.push(current);
}
</script>"""


async def run() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="msedge")
        page = await browser.new_page()
        await page.set_content(HTML)
        adapter = IcveAdapter()
        lesson = Lesson("演示文稿.pptx", "ppt", key="ppt", kind="ppt")
        assert await adapter.read_document(page, lesson, lambda: False)
        assert await page.evaluate("window.visited") == [2, 1, 2, 3, 4]

        await page.goto("about:blank")
        await page.set_content(PDF_HTML)
        pdf = Lesson("2-7等保实施流程-监督检查.pdf", "pdf", key="pdf", kind="pdf")
        assert await adapter.read_document(page, pdf, lambda: False)
        assert await page.evaluate("window.visited") == [3, 2, 1, 2, 3, 4]

        await page.goto("about:blank")
        await page.set_content(HTML)
        assert not await adapter.read_document(page, lesson, lambda: True)
        assert await page.evaluate("window.visited") == [2]

        await page.goto("about:blank")
        await page.set_content(PDF_HTML)
        assert not await adapter.read_document(page, pdf, lambda: True)
        assert await page.evaluate("window.visited") == [3]

        class DocumentAdapter:
            async def enter_lesson(self, _page, _lesson):
                return True

            async def read_document(self, _page, _lesson, should_stop):
                assert not should_stop()
                return True

        clock = SimpleNamespace(reached=lambda _limit: False)
        config = SimpleNamespace(limit_max_minutes=0)
        assert await study_lesson(page, DocumentAdapter(), lesson, clock, config) == "finished"
        assert await study_lesson(page, DocumentAdapter(), pdf, clock, config) == "finished"
        try:
            await study_lesson(page, DocumentAdapter(), lesson, clock, config, lambda: True)
        except StopRequested:
            pass
        else:
            raise AssertionError("PPT 阅读未响应停止指令")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("智慧职教 PPT/PDF：从头逐页阅读、停止响应及主流程分支通过")
