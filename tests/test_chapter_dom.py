"""用接近学习通实际 DOM 的本地页面验证章节测验填写和提交。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate.answer.base import AnswerResult
from coursemate.events import StudyClock
from coursemate.platforms.base import Lesson
from coursemate.platforms.chaoxing import ChaoxingAdapter
from coursemate.runner import study_lesson
from coursemate.workers import ensure_playing, stop_completed_playback


HTML = """<!doctype html><meta charset="utf-8">
<div class="TiMu newTiMu" data="3">
  <div class="Zy_TItle">判断题：已保存</div>
  <li qid="101" aria-checked="true" onclick="addChoice(this)"><label><span class="num_option check_answer" data="true">A</span></label><a class="after">对</a></li>
  <li qid="101" aria-checked="false" onclick="addChoice(this)"><label><span class="num_option" data="false">B</span></label><a class="after">错</a></li>
  <input type="hidden" name="answer101" id="answer101" value="true">
  <input type="hidden" name="answertype101" value="3">
</div>
<div class="TiMu newTiMu" data="1">
  <div class="Zy_TItle">多选题：未作答</div>
  <li qid="102" aria-checked="false" onclick="addMultipleChoice(this)"><span class="num_option_dx" data="D">A</span><a class="after">甲</a></li>
  <li qid="102" aria-checked="false" onclick="addMultipleChoice(this)"><span class="num_option_dx" data="A">B</span><a class="after">乙</a></li>
  <li qid="102" aria-checked="false" onclick="addMultipleChoice(this)"><span class="num_option_dx" data="C">C</span><a class="after">丙</a></li>
  <input type="hidden" name="answer102" id="answer102" value="">
  <input type="hidden" name="answertype102" value="1">
</div>
<button class="btnSave" onclick="sessionStorage.setItem('saved', document.querySelector('#answer102').value); location.reload()">暂时保存</button>
<button class="btnSubmit" onclick="document.querySelector('#workpop').style.display='block'">提交</button>
<div id="workpop" style="display:none"><div id="popcontent">确认提交？</div><button id="popok" onclick="document.querySelectorAll('.TiMu').forEach(x => { x.classList.add('ans-cc'); x.insertAdjacentHTML('beforeend', '<div class=newAnswerBx>答案</div>') }); document.querySelector('.btnSubmit').remove(); document.querySelector('#workpop').style.display='none'">提交</button></div>
<script>
const saved = sessionStorage.getItem('saved');
if (saved) {
  document.querySelector('#answer102').value = saved;
  document.querySelectorAll('li[qid="102"]').forEach(li => {
    if (saved.includes(li.querySelector('span').getAttribute('data'))) {
      li.setAttribute('aria-checked', 'true');
      li.querySelector('span').classList.add('check_answer_dx');
    }
  });
}
function addChoice(li) {
  document.querySelectorAll('li[qid="101"]').forEach(x => { x.setAttribute('aria-checked', 'false'); x.querySelector('span').classList.remove('check_answer') });
  li.setAttribute('aria-checked', 'true');
  li.querySelector('span').classList.add('check_answer');
  document.querySelector('#answer101').value = li.querySelector('span').getAttribute('data');
}
function addMultipleChoice(li) {
  const active = li.getAttribute('aria-checked') !== 'true';
  li.setAttribute('aria-checked', String(active));
  li.querySelector('span').classList.toggle('check_answer_dx', active);
  document.querySelector('#answer102').value = [...document.querySelectorAll('li[qid="102"][aria-checked="true"]')].map(x => x.querySelector('span').getAttribute('data')).join('');
}
if (sessionStorage.getItem('nativeMode')) {
  document.querySelector('.btnSubmit').onclick = () => {
    if (confirm('确认提交？')) {
      document.querySelectorAll('.TiMu').forEach(row => {
        row.classList.add('ans-cc');
        row.insertAdjacentHTML('beforeend', '<div class=newAnswerBx>答案</div>');
      });
      document.querySelector('.btnSubmit').remove();
    }
  };
}
if (sessionStorage.getItem('fallbackMode')) {
  document.querySelector('.btnSubmit').onclick = () => fetch('/work/validate');
}
function submitCheckTimes() {
  document.querySelectorAll('.TiMu').forEach(row => {
    row.classList.add('ans-cc');
    row.insertAdjacentHTML('beforeend', '<div class=newAnswerBx>答案</div>');
  });
  document.querySelector('.btnSubmit').remove();
}
</script>"""

NESTED_QUIZ_HTML = HTML.replace(
    "onclick=\"document.querySelector('#workpop').style.display='block'\"",
    "onclick=\"top.document.querySelector('#workpop').style.display='block'\"",
)
NESTED_HTML = """<!doctype html><meta charset="utf-8">
<div id="workpop" style="display:none">
  <div id="popcontent">确认提交？</div>
  <a id="popok" href="javascript:;" onclick="document.querySelector('iframe').contentDocument.querySelectorAll('.TiMu').forEach(row => { row.classList.add('ans-cc'); row.insertAdjacentHTML('beforeend', '<div class=newAnswerBx>答案</div>') }); document.querySelector('#workpop').style.display='none'">提交</a>
</div>
<iframe src="/quizframe" style="width:800px;height:700px"></iframe>"""


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = await browser.new_page()
            await page.route("https://coursemate.test/quiz", lambda route: route.fulfill(body=HTML, content_type="text/html"))
            await page.route("https://coursemate.test/quizframe", lambda route: route.fulfill(body=NESTED_QUIZ_HTML, content_type="text/html"))
            await page.route("https://coursemate.test/nested", lambda route: route.fulfill(body=NESTED_HTML, content_type="text/html"))
            await page.route("https://coursemate.test/work/validate", lambda route: route.fulfill(json={"status": 3}))
            await page.goto("https://coursemate.test/quiz")
            adapter = ChaoxingAdapter()
            questions = await adapter._extract_chapter_questions(page)
            assert len(questions) == 2
            assert questions[0].selected_keys == ["A"]
            assert questions[1].selected_keys == []
            assert not await adapter.fill_answer(page, questions[0], ["B"], AnswerResult())
            assert await page.locator("#answer101").input_value() == "true"
            assert await adapter.fill_answer(page, questions[1], ["A", "C"], AnswerResult())
            assert await page.locator("#answer102").input_value() == "DC"
            assert await adapter._chapter_answered_count(page) == (2, 2)
            assert await adapter.submit_answer(page, auto_submit=True)
            assert await page.locator(".TiMu.ans-cc .newAnswerBx").count() == 2

            await page.evaluate("sessionStorage.removeItem('saved')")
            await page.goto("https://coursemate.test/quiz")
            fresh = await adapter._extract_chapter_questions(page)
            assert fresh[1].selected_keys == []
            assert await adapter.fill_answer(page, fresh[1], ["B"], AnswerResult())
            assert await adapter.submit_answer(page, auto_submit=False)
            assert await page.locator("#answer102").input_value() == "A"

            await page.evaluate("sessionStorage.setItem('nativeMode', '1')")
            assert await adapter.submit_answer(page, auto_submit=True)

            await page.evaluate("sessionStorage.removeItem('nativeMode'); sessionStorage.setItem('fallbackMode', '1')")
            await page.goto("https://coursemate.test/quiz")
            assert await adapter.submit_answer(page, auto_submit=True)
            assert await page.locator(".TiMu.ans-cc .newAnswerBx").count() == 2
            await page.evaluate("sessionStorage.removeItem('fallbackMode')")

            await page.evaluate("sessionStorage.removeItem('nativeMode'); sessionStorage.removeItem('saved')")
            await page.goto("https://coursemate.test/nested")
            nested = await adapter._extract_chapter_questions(page)
            assert len(nested) == 2
            assert await adapter.fill_answer(page, nested[1], ["B"], AnswerResult())
            assert await adapter.submit_answer(page, auto_submit=True)

            await page.set_content("""<div id="cur1"><span class="jobUnfinishCount" value="1">1</span></div>
                <iframe id="cards" srcdoc='<div class="ans-attach-ct ans-job-finished" style="width:100px;height:100px"></div>'></iframe>
                <video></video>""")
            await page.frame_locator("#cards").locator(".ans-job-finished").wait_for()
            lesson = Lesson("已完成视频，但章节测验未完成", page.locator("#cur1"))
            assert await adapter.lesson_finished(page, lesson)
            assert await study_lesson(
                page, adapter, lesson, StudyClock(),
                type("Settings", (), {"limit_max_minutes": 0})(),
            ) == "finished"
            await page.locator("video").evaluate("""video => {
                video.playCalls = 0;
                video.pauseCalls = 0;
                video.play = () => { video.playCalls++; return Promise.resolve(); };
                video.pause = () => { video.pauseCalls++; };
                Object.defineProperty(video, 'paused', {value: true, configurable: true});
            }""")
            assert await ensure_playing(page)
            await page.locator("video").evaluate("video => Object.defineProperty(video, 'paused', {value: false, configurable: true})")
            await stop_completed_playback(page)
            await page.locator("video").evaluate("video => video.dispatchEvent(new Event('pause'))")
            assert await page.locator("video").evaluate("video => video.playCalls") == 1
            assert await page.locator("video").evaluate("video => video.pauseCalls") == 1
            await page.frame_locator("#cards").locator(".ans-job-finished").evaluate("node => node.remove()")
            assert not await adapter.lesson_finished(page, lesson)
            print("章节测验真实 DOM：答案保留、两种提交确认、暂存及已完成视频均通过")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
