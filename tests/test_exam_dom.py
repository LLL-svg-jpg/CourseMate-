"""学习通独立考试的离线 DOM 回归；不访问真实考试。"""
from __future__ import annotations

import asyncio
import sys
import tomllib
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate.answer.base import AnswerResult
from coursemate import exam
from coursemate.exam import extract_exam_questions, fill_exam_answer, is_exam_url, study_exam
from coursemate.config_writer import dump_config


HTML = """<!doctype html><meta charset=utf-8><div>共1题</div>
<div class=questionLi>
  <input name=type1 value=0 type=hidden>
  <div class=splitS-left><div class=mark_name>第一题：请选择正确项</div></div>
  <div class=answerBg>
    <div><span class=answer_p onclick="this.classList.toggle('check_answer')">A. 甲</span></div>
    <div><span class=answer_p onclick="this.classList.toggle('check_answer')">B. 乙</span></div>
  </div>
</div>
<button onclick="window.submitted = true">提交交卷</button>
"""

PAGED_HTML = """<!doctype html><meta charset=utf-8>
<div class=questionLi id=q1><input name=type1 value=0 type=hidden>
  <div class=splitS-left><div class=mark_name>第一页</div></div>
  <div class=answerBg><div><span class=answer_p onclick="this.classList.toggle('check_answer')">A. 甲</span></div>
  <div><span class=answer_p onclick="this.classList.toggle('check_answer')">B. 乙</span></div></div>
</div>
<div class=questionLi id=q2 style="display:none"><input name=type2 value=0 type=hidden>
  <div class=splitS-left><div class=mark_name>第二页</div></div>
  <div class=answerBg><div><span class=answer_p onclick="this.classList.toggle('check_answer')">A. 丙</span></div>
  <div><span class=answer_p onclick="this.classList.toggle('check_answer')">B. 丁</span></div></div>
</div>
<button id=next onclick="getTheNextQuestion(1)">下一题</button>
<button onclick="window.submitted = true">提交交卷</button>
<script>function getTheNextQuestion() {
  document.querySelector('#q1').style.display='none';
  document.querySelector('#q2').style.display='block';
  document.querySelector('#next').style.display='none';
}</script>
"""

WHOLE_HTML = """<!doctype html><meta charset=utf-8><div>共2题</div>
<div class=questionLi><h3>整卷第一题</h3><input name=type1 value=0 type=hidden>
  <div class=answerBg><div><span class=answer_p onclick="this.classList.toggle('check_answer')">A. 甲</span></div>
  <div><span class=answer_p onclick="this.classList.toggle('check_answer')">B. 乙</span></div></div>
</div>
<div class=questionLi><p>整卷第二题</p><input name=type2 value=0 type=hidden>
  <div class=answerBg><div><span class=answer_p onclick="this.classList.toggle('check_answer')">A. 丙</span></div>
  <div><span class=answer_p onclick="this.classList.toggle('check_answer')">B. 丁</span></div></div>
</div>
<button onclick="window.submitted = true">提交交卷</button>
"""

MULTI_HTML = """<!doctype html><meta charset=utf-8><div>共1题</div>
<div class=questionLi><h3>最后一题：多选</h3><input name=type85 value=1 type=hidden>
  <input id=answer85 name=answer85 type=hidden value="">
  <div class=answerBg>
    <div><span class=answer_p onclick="this.classList.add('check_answer')">A. 甲</span><input type=checkbox value=A onchange="saveAnswers()"></div>
    <div><span class=answer_p onclick="this.classList.add('check_answer')">B. 乙</span><input type=checkbox value=B onchange="saveAnswers()"></div>
    <div><span class=answer_p onclick="this.classList.add('check_answer')">C. 丙</span><input type=checkbox value=C onchange="saveAnswers()"></div>
  </div>
</div>
<script>function saveAnswers() {
  document.querySelector('#answer85').value = [...document.querySelectorAll('input[type=checkbox]:checked')].map(x => x.value).join('');
  sessionStorage.setItem('exam-answer', document.querySelector('#answer85').value);
}
const saved = sessionStorage.getItem('exam-answer') || '';
document.querySelector('#answer85').value = saved;
document.querySelectorAll('input[type=checkbox]').forEach(input => {
  input.checked = saved.includes(input.value);
});</script>
"""


class Provider:
    async def solve(self, question):
        return AnswerResult(option_keys=["B"], confidence=0.9, source="test")


class Cache:
    def get(self, question):
        return None


class MultiProvider:
    async def solve(self, question):
        return AnswerResult(option_keys=["A", "C"], confidence=0.9, source="test")


async def check() -> None:
    assert tomllib.loads(dump_config({}))["answer"]["exam_auto_submit"] is False
    assert tomllib.loads(dump_config({"exam_auto_submit": True}))["answer"]["exam_auto_submit"] is True
    assert is_exam_url("https://mooc1.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?id=1")
    assert is_exam_url("https://mooc-ans.chaoxing.com/mooc-ans/exam/test/reVersionTestStartNew")
    assert not is_exam_url("https://mooc1.chaoxing.com/mycourse/studentstudy")
    assert not is_exam_url("https://evil.example/exam/test/reVersionTestStartNew")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        await page.set_content(HTML)
        questions = await extract_exam_questions(page)
        assert len(questions) == 1 and questions[0].qtype == "single"
        assert [option.text for option in questions[0].options] == ["甲", "乙"]
        assert await fill_exam_answer(page, questions[0], ["B"])
        assert (await extract_exam_questions(page))[0].selected_keys == ["B"]
        assert not await fill_exam_answer(page, questions[0], ["A"])
        assert (await extract_exam_questions(page))[0].selected_keys == ["B"]

        url = "https://mooc1.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?id=offline"
        await page.route(url, lambda route: route.fulfill(body=HTML, content_type="text/html"))
        assert await study_exam(page, url, Provider(), Cache(), False, False, lambda: False)
        assert await page.evaluate("window.submitted") is None
        assert (await extract_exam_questions(page))[0].selected_keys == ["B"]
        assert await study_exam(page, url, Provider(), Cache(), False, True, lambda: False)
        assert await page.evaluate("window.submitted") is True
        await page.unroute(url)
        await page.route(url, lambda route: route.fulfill(body=PAGED_HTML, content_type="text/html"))
        assert await study_exam(page, url, Provider(), Cache(), False, True, lambda: False)
        assert await page.evaluate("window.submitted") is None
        assert await page.locator("#q2 .check_answer").count() == 1
        messages = []
        old_info = exam.logger.info
        exam.logger.info = lambda message, **kwargs: messages.append(message)
        try:
            assert await study_exam(page, url, Provider(), Cache(), False, False, lambda: False)
        finally:
            exam.logger.info = old_info
        assert any("本轮第 2 题" in message for message in messages)
        await page.unroute(url)
        await page.route(url, lambda route: route.fulfill(body=WHOLE_HTML, content_type="text/html"))
        assert await study_exam(page, url, Provider(), Cache(), False, False, lambda: False)
        assert await page.locator(".questionLi .check_answer").count() == 2
        assert await page.evaluate("window.submitted") is None
        assert await study_exam(page, url, Provider(), Cache(), False, True, lambda: False)
        assert await page.evaluate("window.submitted") is True
        await page.unroute(url)
        await page.route(url, lambda route: route.fulfill(
            body=WHOLE_HTML.replace("共2题", "共3题"), content_type="text/html"
        ))
        assert await study_exam(page, url, Provider(), Cache(), False, True, lambda: False)
        assert await page.evaluate("window.submitted") is None
        await page.unroute(url)
        await page.route(url, lambda route: route.fulfill(body=MULTI_HTML, content_type="text/html"))
        await page.goto(url)
        assert (await extract_exam_questions(page))[0].selected_keys == []
        assert await study_exam(page, url, MultiProvider(), Cache(), False, False, lambda: False)
        assert await page.locator('#answer85').input_value() == "AC"
        assert (await extract_exam_questions(page))[0].selected_keys == ["A", "C"]
        await page.reload()
        assert (await extract_exam_questions(page))[0].selected_keys == ["A", "C"]
        await page.unroute(url)
        await page.route(url, lambda route: route.fulfill(
            body=MULTI_HTML.replace("document.querySelector('#answer85').value =", "window.unsaved ="),
            content_type="text/html",
        ))
        messages = []
        old_warn = exam.logger.warn
        exam.logger.warn = lambda message, **kwargs: messages.append(message)
        try:
            await page.evaluate("sessionStorage.clear()")
            assert await study_exam(page, url, MultiProvider(), Cache(), False, False, lambda: False)
        finally:
            exam.logger.warn = old_warn
        assert any("答案字段仍为空" in message for message in messages)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(check())
    print("独立考试离线 DOM 测试通过")
