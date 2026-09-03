"""平台适配器抽象。

一个平台一个子类，只写选择器和差异化流程，核心调度逻辑不动。
新增平台的成本 = 一个文件。

契约分三组：
- 刷课主线：登录 → 打开课程 → 遍历章节 → 播放 → 读进度 → 切下一节
- 答题支线：检测弹题 → 提取 → 填答案 → 提交/保存 → 关闭
- 风控：检测人机验证（只检测并交还人工，不做破解）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from playwright.async_api import BrowserContext, Frame, Page

from ..answer.base import AnswerResult, Question


class Lesson:
    """一个章节/任务点。

    handle 是平台自己的定位对象（ElementHandle 或 URL），核心逻辑不解释它，
    只负责传回给适配器。
    """

    def __init__(self, title: str, handle: Any, finished: bool = False):
        self.title = title
        self.handle = handle
        self.finished = finished

    def __repr__(self) -> str:
        return f"<Lesson {self.title!r} finished={self.finished}>"


class PlatformAdapter(ABC):
    name: str = "base"
    login_url: str = ""
    # 视频所在的 frame。超星等平台把播放器塞在多层 iframe 里，
    # 所以所有涉及 video 的操作都要先经过 video_frame() 而不是直接用 page。
    video_in_iframe: bool = False

    @classmethod
    @abstractmethod
    def match(cls, url: str) -> bool:
        """本适配器是否认领这个课程 URL。"""

    # ---------- 刷课主线 ----------

    @abstractmethod
    async def is_logged_in(self, page: Page) -> bool:
        ...

    @abstractmethod
    async def login(self, page: Page, context: BrowserContext, username: str, password: str) -> None:
        """执行登录。账号密码为空时应停下来等用户手动完成。"""

    @abstractmethod
    async def open_course(self, page: Page, url: str) -> str:
        """打开课程页，返回课程标题。"""

    @abstractmethod
    async def list_lessons(self, page: Page) -> list[Lesson]:
        """列出全部章节。已完成的要标 finished=True，便于跳过。"""

    @abstractmethod
    async def enter_lesson(self, page: Page, lesson: Lesson) -> bool:
        """进入某章节。返回是否成功进入可播放状态。"""

    @abstractmethod
    async def get_progress(self, page: Page) -> str:
        """返回当前章节进度的可读字符串，例如 '68%'。读不到返回空串。"""

    @abstractmethod
    async def lesson_finished(self, page: Page, lesson: Lesson) -> bool:
        """当前章节是否已完成，决定主循环是否推进到下一节。"""

    async def video_frame(self, page: Page) -> Page | Frame:
        """返回真正承载 <video> 的上下文。默认就是主页面。"""
        return page

    async def prepare_page(self, page: Page) -> None:
        """进入课程后的一次性处理：关弹窗、切布局等。默认无操作。"""
        return None

    # ---------- 答题支线 ----------

    async def detect_question(self, page: Page) -> bool:
        """页面上是否正弹着题目。默认平台不支持答题。"""
        return False

    async def extract_questions(self, page: Page) -> list[Question]:
        """提取当前弹窗里的所有题目。"""
        return []

    async def fill_answer(
        self, page: Page, question: Question, keys: list[str], result: AnswerResult
    ) -> bool:
        """把答案填进页面。返回是否填写成功。"""
        return False

    async def submit_answer(self, page: Page, auto_submit: bool) -> bool:
        """提交（auto_submit=True）或仅保存。返回是否成功。"""
        return False

    async def clear_selection(self, page: Page, question: Question) -> None:
        """清除当前已选中的选项。

        试错答题时必须先清空再选下一组，否则多选题会越点越多，
        单选题在某些平台上也会保留旧的高亮。
        """
        return None

    async def read_feedback(self, page: Page) -> str:
        """读取平台对本次作答的判定。

        返回 "correct" / "wrong" / "unknown"。
        返回 unknown 时调用方会停止试错——判不出对错还继续点，
        就等于在乱点，不如交还人工。
        """
        return "unknown"

    async def confirm_and_close(self, page: Page) -> bool:
        """答对之后点确认/继续，让视频接着播。返回是否成功。"""
        await self.close_question(page)
        return True

    async def close_question(self, page: Page) -> None:
        """关闭题目弹窗，让视频继续播放。"""
        return None

    # ---------- 风控 ----------

    async def detect_captcha(self, page: Page) -> bool:
        """是否出现人机验证。只检测，不破解——一律交还人工。"""
        return False

    async def captcha_cleared(self, page: Page) -> bool:
        """人机验证是否已被人工处理完毕。"""
        return True


_REGISTRY: list[type[PlatformAdapter]] = []


def register(cls: type[PlatformAdapter]) -> type[PlatformAdapter]:
    """适配器注册装饰器。"""
    _REGISTRY.append(cls)
    return cls


def resolve(url: str) -> PlatformAdapter | None:
    """按 URL 找到对应适配器实例。找不到返回 None。"""
    for cls in _REGISTRY:
        if cls.match(url):
            return cls()
    return None


def supported_platforms() -> list[str]:
    return [cls.name for cls in _REGISTRY]
