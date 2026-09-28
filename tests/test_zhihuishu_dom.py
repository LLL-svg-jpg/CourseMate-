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
from coursemate.runner import StopRequested, study_course
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


class RecordingCache(Cache):
    def __init__(self):
        self.verified = []

    def put(self, question, result):
        self.verified.append(result.option_keys)


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

    async def detect_question(self, page):
        return False

    async def enter_lesson(self, page, lesson):
        return True

    async def process_chapter_test(self, page, provider, cache, use_cache, auto_submit, should_stop):
        self.calls += 1
        return True


class StopOnChapterAdapter(ChapterOnlyAdapter):
    def __init__(self):
        super().__init__()
        self.stopped = False

    async def enter_lesson(self, page, lesson):
        self.stopped = True
        return True


class PopupBlockingAdapter(ChapterOnlyAdapter):
    def __init__(self):
        super().__init__()
        self.popup_open = True
        self.enter_calls = 0

    async def detect_question(self, page):
        return self.popup_open

    async def enter_lesson(self, page, lesson):
        assert not self.popup_open
        self.enter_calls += 1
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
function choose(button) {
  document.querySelectorAll('.topic-item').forEach(node => node.classList.remove('active'));
  button.classList.add('active');
}
function submit() {
  const choice = document.querySelector('.topic-item.active');
  window.trials.push(choice?.textContent || '');
  if (choice?.textContent.startsWith('B'))
    document.querySelector('#playTopic-dialog').remove();
  else choice?.classList.add('wrong');
}
</script>"""

MULTI_POPUP_HTML = """<div id="playTopic-dialog"><div class="el-dialog">
<div class="el-pager"><li class="number">1</li></div>
<p class="topic-title"><span class="title-tit">【多选题】</span>模拟多选</p>
<ul class="topic-list">
  <li class="topic-item" onclick="choose(this)"><span class="topic-option-item">A.</span><div>甲</div></li>
  <li class="topic-item" onclick="choose(this)"><span class="topic-option-item">B.</span><div>乙</div></li>
  <li class="topic-item" onclick="choose(this)"><span class="topic-option-item">C.</span><div>丙</div></li>
  <li class="topic-item" onclick="choose(this)"><span class="topic-option-item">D.</span><div>丁</div></li>
</ul>
<div class="dialog-footer"><div class="btn" onclick="closePopup()">关闭</div></div>
</div></div>
<script>
window.trials = [];
window.silentWrong = false;
function choose(node) {
  node.querySelector('.topic-option-item').classList.toggle('active');
  const selected = [...document.querySelectorAll('.topic-option-item.active')]
    .map(el => el.textContent.trim()[0]).join('');
  document.querySelectorAll('.topic-title .error,.topic-title .right,.answer')
    .forEach(el => el.remove());
  if (selected.length >= 2) {
    window.trials.push(selected);
    if (window.silentWrong && selected !== window.correct) return;
    document.querySelector('.topic-title').insertAdjacentHTML('afterbegin',
      selected === window.correct ? '<span class="right">正确</span>' :
      '<span class="error">错误</span>');
    if (selected !== window.correct) document.querySelector('.topic-list')
      .insertAdjacentHTML('afterend', `<p class="answer">正确答案：<span>${window.correct.split('').join(',')}</span></p>`);
  }
}
function closePopup() {
  if (document.querySelectorAll('.topic-option-item.active').length >= 2)
    document.querySelector('#playTopic-dialog').remove();
}
window.correct = 'AC';
</script>"""

LIVE_SHAPED_POPUP_HTML = """<div id="playTopic-dialog">
<div class="el-dialog">
  <button class="close-btn" onclick="closePopup()">关闭</button>
  <div class="el-pager"><button class="number">1</button></div>
  <p class="topic-title"><span class="title-tit">【单选题】</span>模拟题目</p>
  <ul>
    <li class="topic-item" onclick="choose(0)"><span class="topic-option-item">A.</span><div class="item-topic">甲</div></li>
    <li class="topic-item" onclick="choose(1)"><span class="topic-option-item">B.</span><div class="item-topic">乙</div></li>
  </ul>
  <div class="feedback"><div class="answer-zq"></div></div>
  <span class="dialog-footer"><div class="btn" onclick="closePopup()">关闭</div></span>
</div></div>
<script>
window.picks = [];
window.warningShown = false;
function choose(i) {
  window.picks.push(i);
  document.querySelectorAll('.topic-item').forEach((node, index) => {
    node.querySelector('.topic-option-item').classList.toggle('active', index === i);
    node.querySelector('.item-topic').classList.toggle('active', index === i);
  });
  const target = document.querySelector(window.gradeTarget);
  target.querySelector('.grade')?.remove();
  target.insertAdjacentHTML('afterbegin',
    `<span class="grade ${window.gradeClass} ${i ? 'right' : 'error'}">${i ? '正确' : '错误'}</span>`);
}
function closePopup() {
  if (!window.picks.length && !document.querySelector('.right')) {
    window.warningShown = true; return;
  }
  document.querySelector('#playTopic-dialog').remove();
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
    stopped_chapter = StopOnChapterAdapter()
    try:
        await study_course(object(), stopped_chapter, "https://example.test", StudyClock(),
                           SimpleNamespace(answer_enabled=True, answer_cache=False,
                                           auto_submit=False, limit_max_minutes=0),
                           None, Cache(), lambda: stopped_chapter.stopped)
    except StopRequested:
        pass
    else:
        raise AssertionError("停止后仍进入平时测试")
    assert stopped_chapter.calls == 0
    blocked = PopupBlockingAdapter()
    blocked_task = asyncio.create_task(study_course(
        object(), blocked, "https://example.test", StudyClock(),
        SimpleNamespace(answer_enabled=False, answer_cache=False,
                        auto_submit=False, limit_max_minutes=0),
        None, Cache(), lambda: False))
    await asyncio.sleep(0.1)
    assert blocked.enter_calls == 0
    blocked.popup_open = False
    assert not await asyncio.wait_for(blocked_task, timeout=3)
    assert blocked.enter_calls == 1
    blocked = PopupBlockingAdapter()
    stop = False
    blocked_task = asyncio.create_task(study_course(
        object(), blocked, "https://example.test", StudyClock(),
        SimpleNamespace(answer_enabled=False, answer_cache=False,
                        auto_submit=False, limit_max_minutes=0),
        None, Cache(), lambda: stop))
    await asyncio.sleep(0.1)
    stop = True
    try:
        await asyncio.wait_for(blocked_task, timeout=3)
    except StopRequested:
        pass
    else:
        raise AssertionError("等待视频弹题时未及时停止")
    assert blocked.enter_calls == 0
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = await browser.new_page()
            await page.set_content("""<label class="el-checkbox privacy-checkbox">
                <input type="checkbox" style="width:0;height:0">同意协议</label>""")
            assert await adapter._accept_login_agreement(page)
            assert await page.locator(".privacy-checkbox input").is_checked()
            assert await adapter._accept_login_agreement(page)
            assert await page.locator(".privacy-checkbox input").is_checked()

            await page.set_content("""<div class="dialog"><div class="dialog-read">
                <i class="iconfont iconguanbi" style="display:inline-block;width:18px;height:18px"
                   onclick="this.closest('.dialog').remove()"></i>
                学前必读</div></div>""")
            await adapter.prepare_page(page)
            assert not await page.locator(".dialog").count()
            adapter.is_shared = False
            await page.set_content("""<style>
                .courseRemind.khfaPop { position:fixed; inset:0; z-index:2003; }
                .courseRemind.khfaPop .el-icon-error {
                    position:absolute; right:20px; top:20px; width:20px; height:20px;
                }
                .wxtsPop { position:fixed; inset:0; z-index:4000; }
                </style><div class="courseRemind courseRemindAd" style="display:none">
                <div class="header-slot"><i class="el-icon-error"></i></div></div>
                <div class="courseRemind khfaPop"><div class="header-slot">学前必读</div>
                   <div class="header-slot" style="width:110px;height:28px">
                   <i class="el-icon-error" onclick="this.closest('.courseRemind').remove()"></i></div></div>
                <div class="wxtsPop"><button class="btn primary"
                   onclick="this.closest('.wxtsPop').remove()">我知道了</button></div>""")
            await adapter.prepare_page(page)
            assert not await page.locator(".courseRemind.khfaPop, .wxtsPop").count()
            assert await page.locator(".courseRemind.courseRemindAd").count() == 1
            await page.set_content("""<div class="wxtsPop"><button class="btn primary"
                onclick="this.closest('.wxtsPop').remove()">我知道了</button></div>""")
            await adapter.prepare_page(page)
            assert await page.locator(".wxtsPop").count() == 1
            late_adapter = ZhihuishuAdapter()
            late_adapter.is_shared = False
            await page.set_content("""<style>
                .courseRemind.khfaPop { position:fixed; inset:0; z-index:2003; }
                .el-icon-error { position:absolute; right:20px; top:20px;
                    width:20px; height:20px; }
                </style><button class="chapter-test" onclick="window.chapterClicked=true">平时测试</button>""")
            await late_adapter.prepare_page(page)
            await page.evaluate("""() => document.body.insertAdjacentHTML('beforeend',
                `<div class="courseRemind khfaPop"><div class="header-slot">学前必读</div>
                <i class="el-icon-error" onclick="this.closest('.courseRemind').remove()"></i></div>`)""")
            assert await late_adapter.enter_lesson(
                page, Lesson("平时测试", page.locator(".chapter-test"), kind="chapter")
            )
            assert await page.evaluate("window.chapterClicked") is True
            assert not await page.locator(".courseRemind.khfaPop").count()
            adapter.is_shared = True

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
            adapter.is_shared = False
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

            adapter.is_shared = True
            await page.set_content(MULTI_POPUP_HTML)
            multi_question = (await adapter.extract_questions(page))[0]
            assert multi_question.qtype == "multiple"
            assert await solve_one_question(
                page, adapter,
                SimpleNamespace(answer_cache=False, retry_until_correct=True,
                                auto_submit=False),
                multi_question, NoAiForPopup(), Cache(),
            )
            assert (await page.evaluate("window.trials"))[:2] == ["AB", "AC"]
            assert not await adapter.detect_question(page)

            await page.set_content(MULTI_POPUP_HTML)
            await page.evaluate("window.silentWrong = true")
            silent_question = (await adapter.extract_questions(page))[0]
            assert await solve_one_question(
                page, adapter,
                SimpleNamespace(answer_cache=False, retry_until_correct=True,
                                auto_submit=False),
                silent_question, NoAiForPopup(), Cache(),
            )
            assert (await page.evaluate("window.trials"))[:2] == ["AB", "AC"]
            assert not await adapter.detect_question(page)

            await page.set_content(MULTI_POPUP_HTML)
            await page.evaluate("""() => {
                window.correct = 'ABCDE';
                window.silentWrong = true;
                const list = document.querySelector('.topic-list');
                list.style.cssText = 'max-height:80px;overflow-y:auto';
                list.insertAdjacentHTML('beforeend',
                    '<li class="topic-item" onclick="choose(this)">' +
                    '<span class="topic-option-item">E.</span><div>戊</div></li>');
            }""")
            five_question = (await adapter.extract_questions(page))[0]
            assert len(five_question.options) == 5
            five_cache = RecordingCache()
            assert await solve_one_question(
                page, adapter,
                SimpleNamespace(answer_cache=True, retry_until_correct=True,
                                auto_submit=False),
                five_question, NoAiForPopup(), five_cache,
            )
            assert (await page.evaluate("window.trials"))[-1] == "ABCDE"
            assert five_cache.verified == [list("ABCDE")]
            assert not await adapter.detect_question(page)

            await page.set_content(MULTI_POPUP_HTML)
            await page.evaluate("window.correct = 'ABC'")
            await page.locator(".topic-item").nth(3).evaluate("el => el.remove()")
            three_choice = (await adapter.extract_questions(page))[0]
            cache = RecordingCache()
            assert await solve_one_question(
                page, adapter,
                SimpleNamespace(answer_cache=True, retry_until_correct=True,
                                auto_submit=False),
                three_choice, NoAiForPopup(), cache,
            )
            trials = await page.evaluate("window.trials")
            assert trials[:3] == ["AB", "AC", "BC"] and trials[-1] == "ABC"
            assert cache.verified == [["A", "B", "C"]]
            assert not await adapter.detect_question(page)

            await page.set_content(MULTI_POPUP_HTML)
            await page.evaluate("window.correct = 'ABC'")
            await page.locator(".topic-item").nth(3).evaluate("el => el.remove()")
            closed_wrong = (await adapter.extract_questions(page))[0]
            await page.locator(".topic-item").nth(0).click()
            await page.locator(".topic-item").nth(1).click()
            assert await adapter.read_feedback(page) == "wrong"
            await page.locator(".dialog-footer .btn").click()
            assert not await adapter.detect_question(page)
            assert await adapter.read_feedback(page) == "unknown"
            assert not await solve_one_question(
                page, adapter,
                SimpleNamespace(answer_cache=False, retry_until_correct=True,
                                auto_submit=False),
                closed_wrong, NoAiForPopup(), Cache(),
            )

            for shared, target, grade_class in (
                (True, ".topic-title", ""),
                (False, ".answer-zq", ""),
            ):
                live_adapter = ZhihuishuAdapter()
                live_adapter.is_shared = shared
                await page.set_content(LIVE_SHAPED_POPUP_HTML)
                await page.evaluate(
                    "([target, cls]) => { window.gradeTarget = target; window.gradeClass = cls; }",
                    [target, grade_class],
                )
                if not shared:
                    await page.locator(".dialog-footer").evaluate("el => el.style.display = 'none'")
                assert await live_adapter.detect_question(page)
                assert await live_adapter.read_feedback(page) == "unknown"
                await live_adapter.close_question(page)
                assert await live_adapter.detect_question(page)
                assert not await page.evaluate("window.warningShown")
                await page.evaluate("choose(0); window.picks = []")
                question = (await live_adapter.extract_questions(page))[0]
                assert question.stem == "模拟题目"
                assert await solve_one_question(
                    page, live_adapter,
                    SimpleNamespace(answer_cache=False, retry_until_correct=True,
                                    auto_submit=False),
                    question, NoAiForPopup(), Cache(),
                )
                assert await page.evaluate("window.picks") == [0, 1]
                assert not await live_adapter.detect_question(page)

                await page.set_content(LIVE_SHAPED_POPUP_HTML)
                await page.evaluate(
                    "([target, cls]) => { window.gradeTarget = target; window.gradeClass = cls; "
                    "choose(1); window.picks = []; }",
                    [target, grade_class],
                )
                already_right = (await live_adapter.extract_questions(page))[0]
                assert await solve_one_question(
                    page, live_adapter,
                    SimpleNamespace(answer_cache=False, retry_until_correct=True,
                                    auto_submit=False),
                    already_right, NoAiForPopup(), Cache(),
                )
                assert await page.evaluate("window.picks") == []
                assert not await live_adapter.detect_question(page)

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

            class StopProvider:
                async def solve(self, question):
                    self.stopped = True
                    return AnswerResult(option_keys=["A", "C"], source="test")

            page = await browser.new_page()
            await page.set_content(WORK_HTML)
            stop_provider = StopProvider()
            stop_provider.stopped = False
            assert not await study_work(page, stop_provider, Cache(), False, True,
                                        lambda: stop_provider.stopped)
            assert await page.evaluate("window.saved.length") == 0
            assert not await page.evaluate("window.submitted || false")

            class SlowProvider:
                def __init__(self):
                    self.started = asyncio.Event()
                    self.cancelled = False

                async def solve(self, question):
                    try:
                        self.started.set()
                        await asyncio.Event().wait()
                    finally:
                        self.cancelled = True

            page = await browser.new_page()
            await page.set_content(WORK_HTML)
            slow_provider = SlowProvider()
            stop = asyncio.Event()
            task = asyncio.create_task(study_work(page, slow_provider, Cache(), False,
                                                  True, stop.is_set))
            await asyncio.wait_for(slow_provider.started.wait(), timeout=2)
            stop.set()
            assert not await asyncio.wait_for(task, timeout=1)
            assert slow_provider.cancelled
            assert not await page.evaluate("window.submitted || false")

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
