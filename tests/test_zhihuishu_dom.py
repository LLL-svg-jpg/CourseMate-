"""智慧树共享课目录、弹题和逐题测试的离线 DOM 回归。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate.answer.base import AnswerResult
from coursemate.platforms.zhihuishu import ZhihuishuAdapter
from coursemate.platforms.base import Lesson
from coursemate.runner import study_course
from coursemate.events import StudyClock
from coursemate.workers import solve_one_question
from coursemate.zhihuishu_work import extract_questions, is_work_url, study_work


class Provider:
    async def solve(self, question):
        return AnswerResult(option_keys=["A", "C"] if question.qtype == "multiple" else ["B"],
                            confidence=1, source="test")


class Cache:
    def get(self, question):
        return None


class ChapterOnlyAdapter:
    confirm_catalog_progress = True

    def __init__(self):
        self.calls = 0

    async def open_course(self, page, url):
        return "测试课程"

    async def prepare_page(self, page):
        pass

    async def list_lessons(self, page):
        return [Lesson("平时测试", object(), key="0", kind="chapter")]

    async def active_lesson_key(self, page):
        return ""

    async def enter_lesson(self, page, lesson):
        return True

    async def process_chapter_test(self, page, provider, cache, use_cache, auto_submit, should_stop):
        self.calls += 1
        return True


class ResumeAdapter(ZhihuishuAdapter):
    def __init__(self):
        super().__init__()
        self.resumed_video = False

    async def enter_lesson(self, page, lesson):
        if lesson.kind == "video":
            self.resumed_video = True
            await lesson.handle.evaluate(
                "el => { document.querySelector('.current_play')?.classList.remove('current_play');"
                " el.classList.add('current_play');"
                " el.insertAdjacentHTML('beforeend', '<i class=\"time_icofinish\"></i>'); }"
            )
            return True
        return await super().enter_lesson(page, lesson)


COURSE_HTML = """
<ul class="list">
  <li class="video"><span class="catalogue_title">第一节</span></li>
  <li class="video current_play"><span class="catalogue_title">第二节</span><i class="time_icofinish"></i></li>
  <li class="chapter-test" title="点击测试"><span class="name">平时测试</span></li>
</ul>
<video></video>
<div id="playButton"><span class="bigPlayButton pointer" onclick="window.playClicked=true">播放</span></div>
<div class="speedBox">倍速</div><div class="speedList"><span class="speedTab" rate="1.25" onclick="window.rateClicked=true">1.25</span></div>
"""

POPUP_HTML = """
<div id="playTopic-dialog">
  <div class="el-pager"><button class="number" onclick="show(0)">1</button><button class="number" onclick="show(1)">2</button></div>
  <div class="title-tit">【单选题】</div><div class="topic-title"></div>
  <div class="options"></div>
  <button class="close-btn" onclick="document.querySelector('#playTopic-dialog').remove()">关闭</button>
</div>
<script>
const data=[['第一题','A. 红','B. 蓝'],['第二题','A. 上','B. 下']];
function show(i){
 document.querySelector('.title-tit').textContent=i?'【多选题】':'【单选题】';
 document.querySelector('.topic-title').textContent=data[i][0];
 document.querySelector('.options').innerHTML=data[i].slice(1).map(x=>`<button class="topic-item" onclick="window.picked=(window.picked||[]).concat('${i}:'+this.textContent)">${x}</button>`).join('');
}
show(0);
</script>
"""

WORK_HTML = """
<div class="examPaper_box">
 <div class="examPaper_subject"><div class="subject_type"></div><div class="subject_describe"><div></div></div><div class="subject_node"></div></div>
 <div class="switch-btn-box"><button>上一题</button><button onclick="next()">下一题</button></div>
</div>
<div class="answerCard_list"><ul><li>1</li><li>2</li></ul></div>
<button id="save" onclick="window.saved[n]=[...document.querySelectorAll('.nodeLab input:checked')].map(x=>x.value)">保存</button>
<button id="submit" onclick="window.submitted=true">提交</button>
<script>
const data=[{type:'多选题',name:'多选题一',options:['A. 红','B. 蓝','C. 黄']},
            {type:'单选题',name:'单选题二',options:['A. 东','B. 西']}];
let n=0;
window.saved=[];
function render(){
 const q=data[n];
 document.querySelector('.subject_type').textContent=q.type;
 document.querySelector('.subject_describe>div').textContent=q.name;
 document.querySelector('.subject_node').innerHTML=q.options.map((text,i)=>
 `<label class="nodeLab"><input type="${q.type==='多选题'?'checkbox':'radio'}" name="q${n}" value="${i}">${text}</label>`).join('');
 document.querySelector('.switch-btn-box button:nth-child(2)').disabled=n===data.length-1;
}
function next(){window.saved[n]=[...document.querySelectorAll('.nodeLab input:checked')].map(x=>x.value);n++;render();}
render();
</script>
"""

TRIAL_POPUP_HTML = """<div id="playTopic-dialog">
<div class="topic-title">视频弹题</div>
<button class="topic-item" onclick="choose(this)">A. 错误</button>
<button class="topic-item" onclick="choose(this)">B. 正确</button>
<button class="submit-btn" onclick="submit()">提交</button></div>
<script>
window.trials = [];
function choose(button) { button.classList.toggle('active'); }
function submit() {
  const choice = document.querySelector('.topic-item.active');
  window.trials.push(choice?.textContent || '');
  if (choice?.textContent.startsWith('B'))
    document.querySelector('#playTopic-dialog').remove();
  else choice?.classList.add('wrong');
}
</script>"""


async def main() -> None:
    assert is_work_url("https://stuonline.zhihuishu.com/stuExamWeb.html#/webExamList/dohomework?id=1")
    assert is_work_url("https://stuonline.zhihuishu.com/stuExamWeb.html#/webExamList/doexamination?id=1")
    assert not is_work_url("https://evil.example/stuExamWeb.html#/webExamList/doexamination")
    adapter = ZhihuishuAdapter()
    adapter.is_shared = True
    adapter.ui_playback = True
    adapter.QUESTION_TITLE_SEL = "#playTopic-dialog .topic-title"
    adapter.QUESTION_LIST_SEL = "#playTopic-dialog .el-pager"
    adapter.OPTION_SEL = "#playTopic-dialog .topic-item"
    chapter = ChapterOnlyAdapter()
    assert not await asyncio.wait_for(
        study_course(object(), chapter, "https://example.test", StudyClock(),
                     SimpleNamespace(answer_enabled=True, answer_cache=False,
                                     auto_submit=False, limit_max_minutes=0),
                     None, Cache(), lambda: False), timeout=3)
    assert chapter.calls == 1
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(COURSE_HTML)
            lessons = await adapter.list_lessons(page)
            assert [(item.title, item.finished, item.kind) for item in lessons] == [
                ("第一节", False, "video"), ("第二节", True, "video"),
                ("平时测试", False, "chapter")]
            assert await adapter.active_lesson_key(page) == "1"
            assert not await adapter.lesson_finished(page, lessons[0])
            assert await adapter.lesson_finished(page, lessons[1])
            assert not await adapter.ensure_playing(page)
            assert await page.evaluate("window.playClicked || false") is False
            await page.locator("li.video").nth(1).locator(".time_icofinish").evaluate("el => el.remove()")
            assert await adapter.ensure_playing(page)
            assert await page.evaluate("window.playClicked") is True

            course_url = "https://studyvideoh5.zhihuishu.com/stuStudy?course=offline"
            work_url = "https://stuonline.zhihuishu.com/stuExamWeb.html#/webExamList/dohomework?id=1"
            course_html = COURSE_HTML.replace(
                'class="chapter-test" title=',
                f'class="chapter-test" onclick="location.href=\'{work_url}\'" title=',
            )
            page = await browser.new_page()
            await page.route("**/stuStudy?course=offline",
                             lambda route: route.fulfill(body=course_html,
                                                         content_type="text/html; charset=utf-8"))
            await page.route("**/stuExamWeb.html",
                             lambda route: route.fulfill(body=WORK_HTML,
                                                         content_type="text/html; charset=utf-8"))
            await page.goto(course_url)
            adapter.course_url = course_url
            chapter_lesson = (await adapter.list_lessons(page))[2]
            assert await adapter.enter_lesson(page, chapter_lesson)
            assert page.url == work_url
            assert await adapter.process_chapter_test(page, Provider(), Cache(), False,
                                                      False, lambda: False)
            assert page.url == course_url
            assert not adapter.course_page_lost
            assert (await adapter.list_lessons(page))[0].title == "第一节"

            resume_html = course_html.replace(
                '<li class="video"><span class="catalogue_title">第一节</span></li>',
                '<li class="video"><span class="catalogue_title">第一节</span>'
                '<i class="time_icofinish"></i></li>',
            ).replace(
                '</ul>',
                '<li class="video"><span class="catalogue_title">第三节</span></li></ul>'
                '<div class="source-name">离线课程</div>',
                1,
            )
            resume = ResumeAdapter()
            page = await browser.new_page()
            await page.route("**/stuStudy?course=offline",
                             lambda route: route.fulfill(body=resume_html,
                                                         content_type="text/html; charset=utf-8"))
            await page.route("**/stuExamWeb.html",
                             lambda route: route.fulfill(body=WORK_HTML,
                                                         content_type="text/html; charset=utf-8"))
            assert not await study_course(page, resume, course_url, StudyClock(),
                                      SimpleNamespace(answer_enabled=True, answer_cache=False,
                                                      auto_submit=False, limit_max_minutes=0),
                                      Provider(), Cache(), lambda: False)
            assert resume.resumed_video
            assert (await resume.list_lessons(page))[3].finished

            await page.set_content(POPUP_HTML)
            assert await adapter.detect_question(page)
            questions = await adapter.extract_questions(page)
            assert [q.stem for q in questions] == ["第一题", "第二题"]
            assert [q.index for q in questions] == [0, 1]
            assert [q.qtype for q in questions] == ["single", "multiple"]
            assert await adapter.fill_answer(page, questions[0], ["B"], AnswerResult())
            assert await adapter.fill_answer(page, questions[1], ["A"], AnswerResult())
            assert await page.evaluate("window.picked") == ["0:B. 蓝", "1:A. 上"]

            await page.goto("about:blank")
            await page.set_content(TRIAL_POPUP_HTML)
            trial_question = (await adapter.extract_questions(page))[0]

            class NoAiForPopup:
                async def solve(self, question):
                    raise AssertionError("视频弹窗启用试错时不应调用 AI")

            assert await solve_one_question(
                page, adapter,
                SimpleNamespace(answer_cache=False, retry_until_correct=True,
                                auto_submit=False),
                trial_question, NoAiForPopup(), Cache())
            assert await page.evaluate("window.trials") == ["A. 错误", "B. 正确"]

            page = await browser.new_page()
            await page.set_content(WORK_HTML)
            questions = await extract_questions(page, capture_image=False)
            assert len(questions) == 1 and questions[0].qtype == "multiple"
            assert await study_work(page, Provider(), Cache(), False, False, lambda: False)
            assert await page.evaluate("window.saved[0]") == ["0", "2"]
            assert await page.evaluate("window.saved[1]") == ["1"]
            assert await page.evaluate("window.submitted || false") is False
            assert (await extract_questions(page, capture_image=False))[0].stem == "单选题二"
            assert await page.locator(".nodeLab input:checked").count() == 1

            page = await browser.new_page()
            await page.set_content(WORK_HTML)
            assert await study_work(page, Provider(), Cache(), False, True, lambda: False)
            assert await page.evaluate("window.submitted") is True

            page = await browser.new_page()
            no_save = WORK_HTML.replace(
                '<button id="save" onclick="window.saved[n]=[...document.querySelectorAll(\'.nodeLab input:checked\')].map(x=>x.value)">保存</button>',
                "",
            )
            await page.set_content(no_save)
            assert await study_work(page, Provider(), Cache(), False, True, lambda: False)
            assert await page.evaluate("window.submitted || false") is False

            page = await browser.new_page()
            await page.route("https://stuonline.zhihuishu.com/stuExamWeb.html",
                             lambda route: route.fulfill(body=WORK_HTML,
                                                         content_type="text/html; charset=utf-8"))
            await page.goto("https://stuonline.zhihuishu.com/stuExamWeb.html#/webExamList/doexamination")
            assert await study_work(page, Provider(), Cache(), False, True, lambda: False)
            assert await page.evaluate("window.submitted || false") is False
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
    print("智慧树离线 DOM 回归通过")
