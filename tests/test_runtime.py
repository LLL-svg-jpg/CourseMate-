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
               test_stop_signal, test_config_roundtrip):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
