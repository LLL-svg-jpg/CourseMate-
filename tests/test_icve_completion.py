"""智慧职教以平台目录进度确认完成，并补刷遗漏课件。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.events import StudyClock
from coursemate.platforms.base import Lesson
from coursemate.runner import study_course, run


class Catalog:
    confirm_catalog_progress = True

    def __init__(self, save_first: bool):
        self.saved = set()
        self.visits = []
        self.save_first = save_first

    async def open_course(self, page, url):
        return "测试课程"

    async def prepare_page(self, page):
        pass

    async def list_lessons(self, page):
        return [Lesson(key, key, key in self.saved, key) for key in ("a", "b")]

    async def active_lesson_key(self, page):
        return "b"

    async def confirm_lesson_completion(self, page, lesson):
        return lesson.key in self.saved


async def run_case(save_first: bool):
    catalog = Catalog(save_first)

    async def play(page, adapter, lesson, clock, config, should_stop):
        adapter.visits.append(lesson.key)
        if lesson.key == "b" or (save_first and adapter.visits.count("a") == 2):
            adapter.saved.add(lesson.key)
        return "finished"

    config = SimpleNamespace(answer_enabled=False, limit_max_minutes=0)
    with patch("coursemate.runner.study_lesson", play):
        result = await study_course(object(), catalog, "url", StudyClock(), config, None, None)
    assert result is save_first
    assert catalog.visits == (["b", "a", "a"] if save_first else ["b", "a", "a", "a"])
    assert ("a" in catalog.saved) is save_first


async def main():
    await run_case(True)
    await run_case(False)
    await test_continue_next_address()


async def test_continue_next_address():
    opened = []

    class Playwright:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            pass

    class Adapter:
        confirm_catalog_progress = True
        name = "智慧职教"

        async def is_logged_in(self, page):
            return True

    class Cache:
        def __init__(self, enabled):
            pass

        def close(self):
            pass

    async def launch(*args):
        return object(), object()

    async def incomplete(page, adapter, url, *args):
        opened.append(url)
        if url == "first":
            # 模拟 list_lessons() 的目录核对异常，必须由 run() 捕获后继续第二地址。
            raise RuntimeError("智慧职教目录本次少了未确认完成课件")
        return False

    async def idle(*args):
        await asyncio.sleep(3600)

    config = SimpleNamespace(course_urls=["first", "second"], answer_cache=False,
                             answer_enabled=False, retry_until_correct=False,
                             auto_submit=False, keep_browser_open=False)
    with (patch("coursemate.runner.async_playwright", Playwright),
          patch("coursemate.runner.launch", launch),
          patch("coursemate.runner.resolve", lambda url: Adapter()),
          patch("coursemate.runner.AnswerCache", Cache),
          patch("coursemate.runner.build_provider", lambda config: None),
          patch("coursemate.runner.study_course", incomplete),
          patch("coursemate.runner.playback_worker", idle),
          patch("coursemate.runner.tuning_worker", idle),
          patch("coursemate.runner.captcha_worker", idle),
          patch("coursemate.runner.question_worker", idle),
          patch("coursemate.runner.task_monitor", idle)):
        await run(config)
    assert opened == ["first", "second"]


if __name__ == "__main__":
    asyncio.run(main())
    print("智慧职教进度确认：补刷、失败报告、未完成后继续第二地址通过")
