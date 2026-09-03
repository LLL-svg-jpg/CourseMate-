"""超星学习通 / 泛雅 (chaoxing.com) 适配器。

超星最大的结构难点：视频播放器嵌在多层 iframe 里，层级还随课程类型变化。
硬编码 iframe 选择器是这类脚本最脆的地方——课程一换、平台一改版就全废。

这里改用递归探测：遍历 page.frames，找到真正含 <video> 的那个 frame。
代价是每次多几毫秒，换来的是对层级变化免疫。

注意：本适配器的选择器基于超星公开页面结构编写，但作者手上没有可长期
复核的真实课程账号，首次使用请配合 `python -m coursemate.doctor` 自检。
"""
from __future__ import annotations

import asyncio

from playwright.async_api import BrowserContext, Frame, Page

from ..answer.base import AnswerResult, Option, Question
from ..logger import Logger
from .base import Lesson, PlatformAdapter, register

logger = Logger()


@register
class ChaoxingAdapter(PlatformAdapter):
    name = "超星学习通"
    login_url = "https://passport2.chaoxing.com/login"
    video_in_iframe = True

    CATALOG_SEL = ".posCatalog_select"
    CATALOG_NAME_SEL = ".posCatalog_name"
    COMPLETED_SEL = ".icon_Completed"
    # 超星的滑块/验证弹层
    CAPTCHA_SELECTORS = ("#nc_1_wrapper", ".nc-container", ".geetest_panel")
    # 视频内弹题
    QUIZ_TITLE_SEL = ".ans-videoquiz-title, .videoquiz-title"
    QUIZ_OPTION_SEL = ".ans-videoquiz-opt, .videoquiz-opt"
    QUIZ_SUBMIT_SEL = ".ans-videoquiz-submit, .videoquiz-submit"

    @classmethod
    def match(cls, url: str) -> bool:
        return "chaoxing.com" in url or "edu.cn/mycourse" in url

    # ---------- frame 定位 ----------

    async def video_frame(self, page: Page) -> Page | Frame:
        """递归找出承载 <video> 的 frame。找不到就退回主页面。"""
        for frame in page.frames:
            try:
                if await frame.query_selector("video"):
                    return frame
            except Exception:
                # 跨域或已销毁的 frame 会抛异常，跳过即可
                continue
        return page

    async def _quiz_frame(self, page: Page) -> Page | Frame | None:
        """找出弹题所在的 frame。弹题未必和 video 在同一层。"""
        for frame in page.frames:
            try:
                if await frame.query_selector(self.QUIZ_TITLE_SEL):
                    return frame
            except Exception:
                continue
        return None

    # ---------- 刷课主线 ----------

    async def is_logged_in(self, page: Page) -> bool:
        return "login" not in page.url and "passport" not in page.url

    async def login(self, page: Page, context: BrowserContext, username: str, password: str) -> None:
        await page.goto(self.login_url, wait_until="commit")
        await page.wait_for_timeout(1500)
        if await self.is_logged_in(page):
            logger.info("检测到已登录，跳过登录步骤。")
            return

        if username and password:
            logger.info("正在自动填写账号密码...")
            try:
                await page.wait_for_selector("#phone", state="attached", timeout=10000)
                await page.locator("#phone").fill(username)
                await page.locator("#pwd").fill(password)
                await page.wait_for_timeout(400)
                await page.locator("#loginBtn").click()
                logger.warn("若出现滑块验证，请手动完成——本程序不自动破解验证码。", shift=True)
            except Exception as exc:
                logger.warn(f"自动填写失败，请手动登录：{Logger.summarize(exc)}")
        else:
            logger.warn("未配置账号密码，请在浏览器中手动登录...", shift=True)

        # 等待跳离登录页，超时给足，等人操作
        for _ in range(24 * 3600 // 2):
            await asyncio.sleep(2)
            if await self.is_logged_in(page):
                return

    async def open_course(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="commit")
        await page.wait_for_timeout(2000)
        for sel in (".course-title", ".courseName", "h1.title"):
            try:
                node = await page.query_selector(sel)
                if node:
                    text = (await node.text_content() or "").strip()
                    if text:
                        return text
            except Exception:
                continue
        return (await page.title() or "未知课程").strip()

    async def prepare_page(self, page: Page) -> None:
        """关掉学习提示弹窗。超星进课时常弹"学习任务"提示挡住播放器。"""
        for js in (
            'document.querySelector(".maskDiv")?.remove();',
            'document.querySelector("#imgClose")?.click();',
            'document.querySelector(".popDiv .close")?.click();',
        ):
            try:
                await page.evaluate(js)
            except Exception:
                pass

    async def list_lessons(self, page: Page) -> list[Lesson]:
        await page.wait_for_selector(self.CATALOG_SEL, state="attached", timeout=30000)
        handles = await page.query_selector_all(self.CATALOG_SEL)
        lessons: list[Lesson] = []
        for handle in handles:
            try:
                name_node = await handle.query_selector(self.CATALOG_NAME_SEL)
                title = (await name_node.text_content() or "").strip() if name_node else ""
                title = " ".join(title.split())[:60]
                finished = await handle.query_selector(self.COMPLETED_SEL) is not None
                lessons.append(Lesson(title=title or "未命名章节", handle=handle, finished=finished))
            except Exception:
                continue
        return lessons

    async def enter_lesson(self, page: Page, lesson: Lesson) -> bool:
        try:
            await lesson.handle.click(timeout=10000)
        except Exception as exc:
            logger.warn(f"点击章节失败：{Logger.summarize(exc)}")
            return False
        await page.wait_for_timeout(2500)
        await self.prepare_page(page)

        # 等待 video 在任意 frame 中出现
        for _ in range(20):
            frame = await self.video_frame(page)
            if frame is not page:
                return True
            try:
                if await page.query_selector("video"):
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
        logger.warn("本章节未找到视频，可能是文档/测验类任务点，跳过。")
        return False

    async def get_progress(self, page: Page) -> str:
        frame = await self.video_frame(page)
        try:
            ratio = await frame.evaluate(
                "(() => { const v = document.querySelector('video');"
                " return v && v.duration ? Math.floor(v.currentTime / v.duration * 100) : null; })()"
            )
            if ratio is not None:
                return f"{ratio}%"
        except Exception:
            pass
        return ""

    async def lesson_finished(self, page: Page, lesson: Lesson) -> bool:
        # 先看目录上的完成标记，这是超星的权威判定
        try:
            if await lesson.handle.query_selector(self.COMPLETED_SEL):
                return True
        except Exception:
            pass
        frame = await self.video_frame(page)
        try:
            return bool(
                await frame.evaluate(
                    "(() => { const v = document.querySelector('video');"
                    " return !!(v && v.duration && v.currentTime >= v.duration - 1.5); })()"
                )
            )
        except Exception:
            return False

    # ---------- 答题支线 ----------

    async def detect_question(self, page: Page) -> bool:
        return await self._quiz_frame(page) is not None

    async def extract_questions(self, page: Page) -> list[Question]:
        frame = await self._quiz_frame(page)
        if frame is None:
            return []
        try:
            title_node = await frame.query_selector(self.QUIZ_TITLE_SEL)
            if not title_node:
                return []
            stem = " ".join((await title_node.text_content() or "").split()).strip()
            if not stem:
                return []
            options: list[Option] = []
            for i, node in enumerate(await frame.query_selector_all(self.QUIZ_OPTION_SEL)):
                text = " ".join((await node.text_content() or "").split()).strip()
                if not text:
                    continue
                key = chr(ord("A") + i)
                if len(text) > 2 and text[0].upper().isalpha() and text[1] in ".、．":
                    key = text[0].upper()
                    text = text[2:].strip()
                options.append(Option(key=key, text=text))
            qtype = "judge" if len(options) == 2 and {o.text for o in options} <= {
                "正确", "错误", "对", "错"
            } else ("multiple" if "多选" in stem[:24] else "single")
            return [Question(stem=stem, options=options, qtype=qtype)]
        except Exception as exc:
            logger.debug(f"提取超星弹题失败：{Logger.summarize(exc)}")
            return []

    async def fill_answer(
        self, page: Page, question: Question, keys: list[str], result: AnswerResult
    ) -> bool:
        frame = await self._quiz_frame(page)
        if frame is None or not keys:
            return False
        wanted = {k.upper() for k in keys}
        clicked = 0
        try:
            nodes = await frame.query_selector_all(self.QUIZ_OPTION_SEL)
        except Exception:
            return False
        for i, node in enumerate(nodes):
            key = question.options[i].key.upper() if i < len(question.options) else chr(ord("A") + i)
            if key not in wanted:
                continue
            try:
                await node.click(timeout=3000)
                clicked += 1
                await asyncio.sleep(0.2)
            except Exception:
                continue
        return clicked > 0

    async def submit_answer(self, page: Page, auto_submit: bool) -> bool:
        """超星的视频弹题必须提交才会放行播放，否则视频卡住不动。

        因此这里与智慧树不同：即使 auto_submit=false 也需要提交，
        否则"继续播放"这个核心诉求根本无法达成。会明确告知用户。
        """
        frame = await self._quiz_frame(page)
        if frame is None:
            return False
        if not auto_submit:
            logger.warn(
                "超星视频弹题不提交就无法继续播放，本题将提交以维持刷课。"
                "若不希望自动提交，请把 answer.enabled 设为 false 并手动答题。",
                shift=True,
            )
        try:
            node = await frame.query_selector(self.QUIZ_SUBMIT_SEL)
            if node:
                await node.click(timeout=3000)
                await asyncio.sleep(0.5)
                return True
        except Exception as exc:
            logger.debug(f"提交超星弹题失败：{Logger.summarize(exc)}")
        return False

    async def close_question(self, page: Page) -> None:
        frame = await self._quiz_frame(page)
        if frame is None:
            return
        for sel in (".ans-videoquiz-close", ".videoquiz-close", ".close"):
            try:
                node = await frame.query_selector(sel)
                if node:
                    await node.click(timeout=2000)
                    await asyncio.sleep(0.3)
                    return
            except Exception:
                continue

    # ---------- 风控 ----------

    async def detect_captcha(self, page: Page) -> bool:
        for sel in self.CAPTCHA_SELECTORS:
            try:
                if await page.query_selector(sel):
                    return True
            except Exception:
                continue
        return False

    async def captcha_cleared(self, page: Page) -> bool:
        return not await self.detect_captcha(page)
