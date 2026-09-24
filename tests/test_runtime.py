"""运行时行为测试：选择器多候选回退、停止信号、配置往返。

这几条是最近改动的关键路径，且都无法靠肉眼确认：
- 多候选回退错了，程序会安静地认为"这门课没有章节"
- 停止信号不生效，界面上的停止按钮就是个摆设
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def install_stub() -> None:
    if "playwright" in sys.modules:
        return
    pkg = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    for n in ("Page", "Frame", "BrowserContext", "Browser", "Playwright",
              "ElementHandle", "Locator", "TimeoutError"):
        setattr(api, n, type(n, (object,), {}))
    api.async_playwright = lambda: None  # type: ignore[attr-defined]
    pkg.async_api = api  # type: ignore[attr-defined]
    sys.modules["playwright"] = pkg
    sys.modules["playwright.async_api"] = api


class FakeHandle:
    """模拟 ElementHandle。"""

    def __init__(self, text: str, cls: str = "", children: set[str] | None = None):
        self._text = text
        self._cls = cls
        self._children = children or set()
        self.clicked = 0

    async def text_content(self) -> str:
        return self._text

    async def get_attribute(self, name: str):
        return self._cls if name == "class" else None

    async def query_selector(self, sel: str):
        return object() if sel in self._children else None

    async def click(self, timeout: int = 0) -> None:
        self.clicked += 1


class FakePage:
    """模拟 Page，只实现被测代码用到的方法。"""

    def __init__(self, dom: dict[str, list[FakeHandle]], url: str = "https://x/"):
        self.dom = dom
        self.url = url

    async def query_selector_all(self, sel: str) -> list[FakeHandle]:
        return self.dom.get(sel, [])

    async def query_selector(self, sel: str):
        hits = self.dom.get(sel, [])
        return hits[0] if hits else None

    async def wait_for_selector(self, sel: str, **kw):
        # 组合选择器：任一命中即返回
        for part in [p.strip() for p in sel.split(",")]:
            if self.dom.get(part):
                return self.dom[part][0]
        raise RuntimeError(f"Timeout waiting for selector {sel}")

    async def wait_for_timeout(self, ms: int) -> None:
        return None

    async def evaluate(self, *a, **k):
        return None


def test_selector_fallback() -> None:
    print("\n== 章节选择器多候选回退 ==")
    from coursemate.platforms.zhihuishu import ZhihuishuAdapter

    # 场景：经典选择器 .clearfix.video 落空，新版页面用 .video-item
    adapter = ZhihuishuAdapter()
    page = FakePage({
        ".video-item": [
            FakeHandle("第一章 绪论", "video-item current_play", {".time_icofinish"}),
            FakeHandle("第二章 基础", "video-item"),
        ]
    })
    lessons = asyncio.run(adapter.list_lessons(page))  # type: ignore[arg-type]
    check("落空后回退到下一候选", len(lessons) == 2, f"got {len(lessons)}")
    check("记录实际命中的选择器", adapter.lesson_sel == ".video-item", adapter.lesson_sel)
    check("标题被正确提取", lessons[0].title == "第一章 绪论", lessons[0].title)
    check("已完成标记被识别", lessons[0].finished is True)
    check("未完成不误判", lessons[1].finished is False)

    # 场景：所有候选都落空，必须优雅返回空表而不是抛异常
    adapter2 = ZhihuishuAdapter()
    empty = asyncio.run(adapter2.list_lessons(FakePage({})))  # type: ignore[arg-type]
    check("全部落空返回空表而非崩溃", empty == [])

    # 场景：第一候选就命中，不应继续往下试
    adapter3 = ZhihuishuAdapter()
    page3 = FakePage({
        ".clearfix.video": [FakeHandle("A", "clearfix video")],
        ".video-item": [FakeHandle("B"), FakeHandle("C")],
    })
    lessons3 = asyncio.run(adapter3.list_lessons(page3))  # type: ignore[arg-type]
    check("优先使用排在前面的候选", adapter3.lesson_sel == ".clearfix.video" and len(lessons3) == 1)


def test_lesson_finished_markers() -> None:
    print("\n== 小节完成判定 ==")
    from coursemate.platforms.base import Lesson
    from coursemate.platforms.zhihuishu import ZhihuishuAdapter

    adapter = ZhihuishuAdapter()
    page = FakePage({})

    playing = Lesson("播放中", FakeHandle("x", "video-item current_play"))
    check("仍在播放不算完成", asyncio.run(adapter.lesson_finished(page, playing)) is False)  # type: ignore[arg-type]

    done = Lesson("已切走", FakeHandle("x", "video-item"))
    check("高亮已移交即算完成", asyncio.run(adapter.lesson_finished(page, done)) is True)  # type: ignore[arg-type]

    alt = Lesson("另一种高亮", FakeHandle("x", "lesson playing"))
    check("识别其他高亮写法", asyncio.run(adapter.lesson_finished(page, alt)) is False)  # type: ignore[arg-type]


def test_stop_signal() -> None:
    print("\n== 停止信号 ==")
    import threading

    from coursemate.config import Config
    from coursemate.events import StudyClock
    from coursemate.platforms.base import Lesson
    from coursemate.runner import StopRequested, study_lesson
    from coursemate.platforms.zhihuishu import ZhihuishuAdapter

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "c.toml"
        cfg_path.write_text(
            '[course]\nurls = ["https://x.zhihuishu.com/a"]\nspeed = 1.0\n'
            "limit_max_minutes = 0\n", encoding="utf-8")
        config = Config(cfg_path)

        class NeverFinishAdapter(ZhihuishuAdapter):
            async def enter_lesson(self, page, lesson):
                return True

            async def lesson_finished(self, page, lesson):
                return False

            async def get_progress(self, page):
                return "1%"

        adapter = NeverFinishAdapter()
        lesson = Lesson("永不结束的一节", FakeHandle("x", "current_play"))
        event = threading.Event()

        async def scenario():
            task = asyncio.create_task(
                study_lesson(FakePage({}), adapter, lesson, StudyClock(),  # type: ignore[arg-type]
                             config, event.is_set)
            )
            await asyncio.sleep(0.2)
            event.set()          # 模拟用户点「停止」
            try:
                await asyncio.wait_for(task, timeout=3.0)
                return "no-stop"
            except StopRequested:
                return "stopped"
            except asyncio.TimeoutError:
                return "timeout"

        result = asyncio.run(scenario())
        check("停止信号能中断刷课循环", result == "stopped", f"result={result}")


def test_continuous_play_guard() -> None:
    print("\n== 连续播放守卫 ==")
    from coursemate.workers import ensure_playing

    class FakeFrame:
        def __init__(self):
            self.script = ""

        async def evaluate(self, script):
            self.script = script
            return True

    frame = FakeFrame()
    resumed = asyncio.run(ensure_playing(frame))
    check("暂停时会立即续播", resumed is True)
    check("安装 pause 事件守卫", "addEventListener('pause', resume)" in frame.script)
    check("真正播放结束时不重播", "v.paused && !v.ended" in frame.script)


def test_chaoxing_live_lesson_locators() -> None:
    print("\n== 学习通章节目录重绘 ==")
    from coursemate.platforms.chaoxing import ChaoxingAdapter

    class Row:
        def __init__(self, title, finished=False, item_id="cur1", item_class="", pending=None):
            self.title, self.finished, self.clicked = title, finished, 0
            self.item_id, self.item_class = item_id, item_class
            self.pending = pending

    class Locator:
        def __init__(self, page, index=None, child=""):
            self.page, self.index, self.child = page, index, child

        @property
        def first(self): return self

        def nth(self, index): return Locator(self.page, index)
        def locator(self, selector): return Locator(self.page, self.index, selector)
        def row(self): return self.page.rows[self.index]

        async def count(self):
            if self.index is None:
                return len(self.page.rows)
            if self.child == ChaoxingAdapter.CATALOG_NAME_SEL:
                return 1
            if self.child == ".jobUnfinishCount":
                return int(self.row().pending is not None)
            return int(self.row().finished)

        async def text_content(self): return self.row().title
        async def click(self, timeout=0): self.row().clicked += 1
        async def get_attribute(self, name):
            if self.child == ".jobUnfinishCount" and name == "value":
                return self.row().pending
            return self.row().item_id if name == "id" else self.row().item_class

    class Page:
        def __init__(self, rows): self.rows = rows
        async def wait_for_selector(self, *args, **kwargs): return None
        def locator(self, selector): return Locator(self)

    old_rows = [Row("第一节"), Row("第二节")]
    page = Page(old_rows)
    adapter = ChaoxingAdapter()
    lessons = asyncio.run(adapter.list_lessons(page))
    new_rows = [Row("第一节", finished=True), Row("第二节")]
    page.rows = new_rows
    asyncio.run(lessons[1].handle.click())
    check("目录重绘后点击的是新节点", new_rows[1].clicked == 1 and old_rows[1].clicked == 0)
    check("目录重绘后的完成标记仍可读取",
          asyncio.run(adapter.lesson_finished(page, lessons[0])) is True)  # type: ignore[arg-type]

    page.rows = [Row("第一章", item_id="chapter1", item_class="firstLayer"),
                 Row("第一节", item_id="cur1")]
    filtered = asyncio.run(adapter.list_lessons(page))
    check("纯目录标题不会被当成视频", [item.title for item in filtered] == ["第一节"])

    page.rows = [Row("待测验", item_id="cur1", pending="1"),
                 Row("全部完成", item_id="cur2", pending="0")]
    pending = asyncio.run(adapter.list_lessons(page))
    check("学习通待完成任务数参与完成判定",
          [item.finished for item in pending] == [False, True])


def test_chaoxing_chapter_test_detection() -> None:
    print("\n== 学习通章节测试识别 ==")
    from coursemate.platforms.base import Lesson
    from coursemate.platforms.chaoxing import ChaoxingAdapter

    class VisibleNode:
        async def is_visible(self): return True

    class QuizFrame:
        async def query_selector(self, selector):
            return VisibleNode() if selector == ChaoxingAdapter.CHAPTER_TEST_SEL else None

    class Page:
        def __init__(self): self.frames = [QuizFrame()]
        async def wait_for_timeout(self, ms): return None
        async def query_selector(self, selector): return None

    class Handle:
        async def click(self, timeout=0): return None

    class Adapter(ChaoxingAdapter):
        async def prepare_page(self, page): return None
        async def video_frame(self, page): return page

    entered = asyncio.run(Adapter().enter_lesson(Page(), Lesson("章节测试", Handle())))  # type: ignore[arg-type]
    check("独立章节测验交给主流程处理而不是误判无视频", entered is True)


def test_manual_lesson_switch() -> None:
    print("\n== 学习通手动切课接管 ==")
    from coursemate.events import StudyClock
    from coursemate.platforms.base import Lesson
    from coursemate.runner import study_lesson

    class Adapter:
        async def detect_chapter_test(self, page): return False
        async def enter_lesson(self, page, lesson): return True
        async def active_lesson_key(self, page): return "cur-other"
        async def lesson_finished(self, page, lesson): return False
        async def get_progress(self, page): return "10%"

    class Cfg:
        limit_max_minutes = 0

    result = asyncio.run(study_lesson(
        object(), Adapter(), Lesson("原章节", object(), key="cur-old"),
        StudyClock(), Cfg(),  # type: ignore[arg-type]
    ))
    check("用户切到任意章节后旧等待循环会让出控制", result == "switched", result)


def test_previous_chapter_test_does_not_block_next_lesson() -> None:
    print("\n== 上一章测验页不阻塞下一章 ==")
    from coursemate.events import StudyClock
    from coursemate.platforms.base import Lesson
    from coursemate.runner import study_lesson

    class Adapter:
        entered = False
        async def enter_lesson(self, page, lesson):
            self.entered = True
            return True
        async def detect_chapter_test(self, page): return True

    class Cfg:
        limit_max_minutes = 0

    adapter = Adapter()
    result = asyncio.run(study_lesson(
        object(), adapter, Lesson("下一章", object(), key="cur-next"),
        StudyClock(), Cfg(),  # type: ignore[arg-type]
    ))
    check("即使页面残留上一章测验也会先进入目标章节", adapter.entered is True)
    check("进入后检测当前章节测验", result == "chapter", result)


def test_course_rebases_after_manual_switch() -> None:
    print("\n== 学习通主循环跟随手动章节 ==")
    from coursemate import runner
    from coursemate.events import StudyClock
    from coursemate.platforms.base import Lesson

    lessons = [Lesson("第一节", object(), key="cur1"),
               Lesson("第二节", object(), key="cur2")]

    class Adapter:
        active = "cur1"
        async def open_course(self, page, url): return "测试课"
        async def prepare_page(self, page): return None
        async def list_lessons(self, page): return lessons
        async def active_lesson_key(self, page): return self.active
        async def open_chapter_test(self, page): return False

    class Config:
        answer_enabled = False
        limit_max_minutes = 0

    class Cache:
        pass

    adapter, visited = Adapter(), []
    original = runner.study_lesson

    async def fake_study(page, current_adapter, lesson, clock, config, should_stop):
        visited.append(lesson.title)
        if lesson.key == "cur1":
            adapter.active = "cur2"
            return "switched"
        return "finished"

    runner.study_lesson = fake_study
    try:
        asyncio.run(runner.study_course(
            object(), adapter, "https://example.test", StudyClock(),
            Config(), None, Cache(),  # type: ignore[arg-type]
        ))
    finally:
        runner.study_lesson = original
    check("手动从第一节切到第二节后从第二节接着跑",
          visited == ["第一节", "第二节"], str(visited))


def test_course_continues_after_chapter_fallback() -> None:
    print("\n== 章节测验兜底暂存后继续下一章 ==")
    from coursemate import runner
    from coursemate.events import StudyClock
    from coursemate.platforms.base import Lesson

    lessons = [Lesson("第一节", object(), key="cur1"),
               Lesson("第二节", object(), key="cur2")]

    class Adapter:
        async def open_course(self, page, url): return "测试课"
        async def prepare_page(self, page): return None
        async def list_lessons(self, page): return lessons
        async def active_lesson_key(self, page): return "cur1"
        async def open_chapter_test(self, page): return False

    class Config:
        answer_enabled = True
        limit_max_minutes = 0

    visited = []
    original_study = runner.study_lesson
    original_solve = runner.solve_chapter_test_once

    async def fake_study(page, adapter, lesson, clock, config, should_stop):
        visited.append(lesson.title)
        return "chapter"

    async def fake_solve(page, adapter, config, provider, cache):
        return False

    runner.study_lesson = fake_study
    runner.solve_chapter_test_once = fake_solve
    try:
        asyncio.run(runner.study_course(
            object(), Adapter(), "https://example.test", StudyClock(),
            Config(), None, object(),  # type: ignore[arg-type]
        ))
    finally:
        runner.study_lesson = original_study
        runner.solve_chapter_test_once = original_solve
    check("章节处理返回后继续下一章",
          visited == ["第一节", "第二节"], str(visited))


def test_completed_course_does_not_replay() -> None:
    print("\n== 已完成课程不重播 ==")
    from coursemate import runner
    from coursemate.events import StudyClock
    from coursemate.platforms.base import Lesson

    class Adapter:
        async def open_course(self, page, url): return "已完成课程"
        async def prepare_page(self, page): return None
        async def list_lessons(self, page):
            return [Lesson("第一节", object(), finished=True, key="cur1")]

    original = runner.study_lesson
    visited = []

    async def fake_study(*args):
        visited.append(True)
        return "finished"

    runner.study_lesson = fake_study
    try:
        result = asyncio.run(runner.study_course(
            object(), Adapter(), "https://example.test", StudyClock(),
            object(), None, object(),  # type: ignore[arg-type]
        ))
    finally:
        runner.study_lesson = original
    check("目录已全部完成时不进入视频重播", result and not visited)


def test_chaoxing_replayed_tail_skip() -> None:
    print("\n== 学习通重复尾段跳过 ==")
    from coursemate.platforms.chaoxing import ChaoxingAdapter

    class Frame:
        def __init__(self, result): self.result, self.script = result, ""
        async def evaluate(self, script): self.script = script; return self.result

    frame = Frame(58.5)
    skipped = asyncio.run(ChaoxingAdapter()._skip_replayed_tail(frame))  # type: ignore[arg-type]
    check("恢复到最后一分钟时会跳过重复尾段", abs(skipped - 58.5) < 0.01)
    check("新视频前 15 秒不会误跳", "v.currentTime < 15" in frame.script)
    check("只处理最后 75 秒", "remaining > 75" in frame.script)


def test_chaoxing_blank_page_not_logged_in() -> None:
    print("\n== 学习通登录入口 ==")
    from coursemate.platforms.chaoxing import ChaoxingAdapter

    class Page:
        def __init__(self, url, title): self.url, self._title = url, title
        async def title(self): return self._title

    adapter = ChaoxingAdapter()
    blank = asyncio.run(adapter.is_logged_in(Page("about:blank", "")))  # type: ignore[arg-type]
    course = asyncio.run(adapter.is_logged_in(
        Page("https://mooc1.chaoxing.com/mycourse/studentstudy", "学生学习页面")))  # type: ignore[arg-type]
    login = asyncio.run(adapter.is_logged_in(
        Page("https://passport2.chaoxing.com/login", "用户登录")))  # type: ignore[arg-type]
    check("新开空白页不会再被误判为已登录", blank is False)
    check("真正课程页仍识别为已登录", course is True)
    check("登录页识别为未登录", login is False)


def test_config_roundtrip() -> None:
    print("\n== 配置往返 ==")
    import tempfile
    import tomllib

    from coursemate.config import Config
    from coursemate.config_writer import save_config

    data = {
        "username": "13800000000", "password": 'p@ss"word\\x',
        "channel": "edge", "executable_path": r"C:\Program Files\x\msedge.exe",
        "window_size": (1600, 900), "headless": False,
        "urls": ["https://studyvideoh5.zhihuishu.com/videoStudy.html#/a"],
        "speed": 1.75, "mute": True, "limit_max_minutes": 45,
        "answer_enabled": True, "auto_submit": False, "provider": "anthropic",
        "api_key": "sk-ant-test", "model": "claude-opus-5", "base_url": "",
        "timeout": 45, "cache": True, "log_level": "INFO", "beep_on_captcha": True,
    }
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "config.toml"
        save_config(p, data)
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
        check("含引号密码往返无损", raw["account"]["password"] == 'p@ss"word\\x',
              repr(raw["account"]["password"]))
        check("Windows 路径反斜杠不被吞",
              raw["browser"]["executable_path"] == r"C:\Program Files\x\msedge.exe",
              repr(raw["browser"]["executable_path"]))

        cfg = Config(p)
        check("Config 能读回生成的文件", cfg.course_urls == data["urls"])
        check("edge 被映射为 msedge", cfg.channel == "msedge", cfg.channel)
        check("倍速往返一致", cfg.speed == 1.75)
        check("限时往返一致", cfg.limit_max_minutes == 45)
        check("auto_submit 保持 false", cfg.auto_submit is False)


if __name__ == "__main__":
    print("CourseMate 运行时行为测试")
    install_stub()
    for fn in (test_selector_fallback, test_lesson_finished_markers,
               test_stop_signal, test_continuous_play_guard,
               test_chaoxing_live_lesson_locators, test_chaoxing_chapter_test_detection,
               test_manual_lesson_switch, test_previous_chapter_test_does_not_block_next_lesson,
               test_course_rebases_after_manual_switch,
               test_course_continues_after_chapter_fallback,
               test_completed_course_does_not_replay,
               test_chaoxing_replayed_tail_skip,
               test_chaoxing_blank_page_not_logged_in,
               test_config_roundtrip):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
