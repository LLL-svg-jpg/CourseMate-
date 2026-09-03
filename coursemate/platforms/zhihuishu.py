"""知道智慧树 (zhihuishu.com) 适配器。

选择器来源：CXRunfree/Autovisor (MIT)，那些是在真实页面上跑了三年的验证结果，
直接取用比自己猜靠谱得多。

与 Autovisor 的差别在答题：它检测到弹题后盲选前两个选项把弹窗关掉，
只求视频继续播；这里改成提取题干交给 AI 作答。
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from ..answer.base import AnswerResult, Option, Question
from ..logger import Logger
from .base import Lesson, PlatformAdapter, register

logger = Logger()


@register
class ZhihuishuAdapter(PlatformAdapter):
    name = "知道智慧树"
    # 旧的 passport.zhihuishu.com 只是跳板，会重定向到这里
    login_url = "https://login.zhihuishu.com/?origin=zhs"
    video_in_iframe = False

    # 判定"还在登录流程里"的域名前缀
    LOGIN_HOSTS = ("login.", "passport.")

    # 登录表单选择器（2026-09 在真实页面实测）。
    # 注意：新登录页的 input id 是 el-id-1784-18 这种每次加载都变的动态值，
    # 绝对不能拿 id 当选择器，只能用 name / type / class。
    USERNAME_SEL = 'input[name="mobile"]'
    PASSWORD_SEL = 'input[type="password"]'
    LOGIN_BTN_SEL = ".btn-block__grandient_login"

    # 智慧树同时在跑多套播放页：studyh5 / studyvideoh5 / fusioncourseh5 / hike，
    # DOM 结构并不一致。硬编码单个选择器必然在某些课上落空，
    # 因此这里按候选列表依次尝试，命中哪个就用哪个，并把结果记进日志便于反馈。
    LESSON_CANDIDATES = (
        ".clearfix.video",      # 经典学分课，Autovisor 验证
        ".video-item",          # studyvideoh5 常见
        ".catalogue-item",
        ".chapter-item",
        ".lesson-item",
        "li[class*='video']",
    )
    LESSON_ACTIVE_MARKERS = ("current_play", "active", "on", "playing")
    HIKE_LESSON_SEL = ".file-item"
    HIKE_LESSON_ACTIVE = "active"
    FINISHED_MARKERS = (".time_icofinish", ".icon-finish", ".finished", ".complete")

    CAPTCHA_SEL = ".yidun_modal__title"
    DIALOG_SEL = ".el-dialog"
    QUESTION_TITLE_SEL = ".topic-title"
    QUESTION_LIST_SEL = ".el-scrollbar__view"
    QUESTION_NUMBER_SEL = ".number"
    OPTION_SEL = ".topic-item"
    ANSWERED_SEL = ".answer"

    def __init__(self) -> None:
        self.is_hike = False
        # 实际命中的章节选择器，供日志与后续复用
        self.lesson_sel: str = ""

    @classmethod
    def match(cls, url: str) -> bool:
        return "zhihuishu.com" in url

    # ---------- 刷课主线 ----------

    async def is_logged_in(self, page: Page) -> bool:
        """按域名判断，而不是找某个元素。

        以前用"登录框消失"来判定，结果智慧树换了登录页后旧选择器全部失效，
        元素不存在天然等于 hidden，程序当场认为登录成功，然后拿着未登录的
        会话去开课程页——平台回了 404。这类误判必须从判据上根除。
        """
        host = urlparse(page.url).hostname or ""
        if not host:  # about:blank 等尚未导航的状态
            return False
        return not any(host.startswith(p) for p in self.LOGIN_HOSTS)

    async def login(self, page: Page, context: BrowserContext, username: str, password: str) -> None:
        await page.goto(self.login_url, wait_until="domcontentloaded")
        # 登录页是 SPA，且 passport 域会重定向到 login 域，要等它渲染完
        await page.wait_for_timeout(3000)
        if await self.is_logged_in(page):
            logger.info("检测到已登录，跳过登录步骤。")
            return

        try:
            await page.wait_for_selector(self.USERNAME_SEL, state="visible", timeout=20000)
        except Exception:
            logger.warn(
                "没有认出登录表单，可能是智慧树又改版了。"
                "请在浏览器里手动完成登录，程序会等你。", shift=True)

        if username and password:
            logger.info("正在自动填写账号密码...")
            try:
                await page.fill(self.USERNAME_SEL, username, timeout=10000)
                await page.fill(self.PASSWORD_SEL, password, timeout=10000)
                await page.wait_for_timeout(600)
                await page.click(self.LOGIN_BTN_SEL, timeout=10000)
                logger.info("已提交登录，等待跳转...")
            except Exception as exc:
                logger.warn(f"自动登录未能完成，请手动操作：{Logger.summarize(exc)}", shift=True)
            logger.warn(
                "若出现滑块验证或需要勾选用户协议，请手动完成"
                "——本程序不代你接受条款，也不破解验证码。", shift=True)
        else:
            logger.warn("未配置账号密码，请在浏览器窗口中手动登录...", shift=True)

        # 以"离开登录域名"作为成功判据，等人操作所以超时给足
        waited = 0
        while waited < 24 * 3600:
            await asyncio.sleep(2)
            waited += 2
            if await self.is_logged_in(page):
                return
            if waited % 60 == 0:
                logger.info(f"仍在等待登录完成...（已等 {waited // 60} 分钟）")

    # 打开课程页后用来识别"这不是课程页"的特征
    ERROR_HINTS = ("404", "页面不存在", "找不到", "无权限", "not found", "出错了")

    async def open_course(self, page: Page, url: str) -> str:
        self.is_hike = "hike.zhihuishu.com" in url
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # 播放页是 SPA，DOM 就绪不等于内容渲染完
        await page.wait_for_timeout(3500)

        status = response.status if response else 0
        if status >= 400:
            logger.error(f"课程页返回 HTTP {status}。请确认地址是否完整、是否已过期。")

        # 被踢回登录页说明会话没生效，这是 404 之外的另一种常见失败
        if not await self.is_logged_in(page):
            logger.error("打开课程页时被跳回登录页，说明登录状态未生效。")
            return "未登录"

        title_sel = ".course-name" if self.is_hike else ".source-name"
        try:
            node = await page.wait_for_selector(title_sel, timeout=15000)
            title = (await node.text_content() or "").strip()
            if title:
                return title
        except Exception:
            pass

        # 读不到课程名时，检查是不是打开了错误页——否则后面会一路"没有章节"，
        # 让人以为是选择器问题，其实根本没进对页面
        try:
            body = (await page.inner_text("body"))[:400].lower()
        except Exception:
            body = ""
        if any(h in body for h in self.ERROR_HINTS):
            logger.error(
                "打开的页面像是错误页而不是课程页。请核对课程地址：\n"
                "  1) 必须是视频播放页，浏览器地址栏里带 courseId / classId 参数\n"
                "  2) 从课程列表点进去开始播放后，再复制地址栏的完整地址"
            )
            return "打开失败"
        return "未知课程"

    async def prepare_page(self, page: Page) -> None:
        """关掉进入课程时的引导弹窗，否则会挡住播放器。"""
        for js in (
            'document.getElementsByClassName("iconfont iconguanbi")[0]?.click();',
            'document.querySelector(".dialog-close")?.click();',
        ):
            try:
                await page.evaluate(js)
            except Exception:
                pass
        # 屏蔽页面对 video.pause 的调用，防止弹窗关闭后视频不自动恢复
        try:
            await page.evaluate("document.querySelector('video').pause = () => {}")
        except Exception:
            pass

    async def list_lessons(self, page: Page) -> list[Lesson]:
        sel = await self._resolve_lesson_selector(page)
        if not sel:
            logger.error(
                "未能识别章节列表。可能是该课程使用了尚未适配的播放页版本。"
                "请把当前页面地址反馈给作者，或自行往 LESSON_CANDIDATES 里补充选择器。"
            )
            return []

        handles = await page.query_selector_all(sel)
        lessons: list[Lesson] = []
        for handle in handles:
            title = " ".join((await handle.text_content() or "").split())[:60]
            finished = False
            for marker in self.FINISHED_MARKERS:
                try:
                    if await handle.query_selector(marker):
                        finished = True
                        break
                except Exception:
                    continue
            lessons.append(Lesson(title=title or "未命名小节", handle=handle, finished=finished))
        return lessons

    async def _resolve_lesson_selector(self, page: Page) -> str:
        """逐个试候选选择器，返回第一个真能选出元素的。

        智慧树同时在跑多套播放页，硬认一个选择器等于把成功率押在运气上。
        """
        if self.is_hike:
            try:
                await page.wait_for_selector(self.HIKE_LESSON_SEL, state="attached", timeout=20000)
                self.lesson_sel = self.HIKE_LESSON_SEL
                return self.lesson_sel
            except Exception:
                return ""

        # H5 播放页的目录是异步渲染的，先给它加载时间
        try:
            await page.wait_for_selector(
                ", ".join(self.LESSON_CANDIDATES), state="attached", timeout=30000
            )
        except Exception:
            logger.debug("等待章节列表超时，仍逐个候选试一次。")

        for candidate in self.LESSON_CANDIDATES:
            try:
                found = await page.query_selector_all(candidate)
            except Exception:
                continue
            if found:
                self.lesson_sel = candidate
                logger.info(f"章节列表命中选择器 {candidate}，共 {len(found)} 项。")
                return candidate
        return ""

    async def enter_lesson(self, page: Page, lesson: Lesson) -> bool:
        try:
            await lesson.handle.click(timeout=10000)
        except Exception as exc:
            logger.warn(f"点击小节失败：{Logger.summarize(exc)}")
            return False
        markers = (
            (self.HIKE_LESSON_ACTIVE,) if self.is_hike else self.LESSON_ACTIVE_MARKERS
        )
        try:
            await page.wait_for_selector(
                ", ".join(f".{m}" for m in markers), state="attached", timeout=15000
            )
        except Exception:
            # 等不到高亮标记不代表进不去，继续用 video 是否出现来判断
            pass
        await page.wait_for_timeout(1000)
        try:
            await page.wait_for_selector("video", state="attached", timeout=20000)
        except Exception:
            logger.warn("本小节未找到视频元素，可能是文档或作业类任务点。")
            return False
        await self.prepare_page(page)
        return True

    async def get_progress(self, page: Page) -> str:
        """智慧树把学习进度写在 .percent / .study-percent 上。

        读不到就退回按视频播放比例估算——刷课主线不能因为读不到进度就停摆。
        """
        for sel in (".percent", ".study-percent", ".progress-num"):
            try:
                node = await page.query_selector(sel)
                if node:
                    text = (await node.text_content() or "").strip()
                    if text:
                        return text
            except Exception:
                continue
        try:
            ratio = await page.evaluate(
                "(() => { const v = document.querySelector('video');"
                " return v && v.duration ? Math.floor(v.currentTime / v.duration * 100) : null; })()"
            )
            if ratio is not None:
                return f"{ratio}%"
        except Exception:
            pass
        return ""

    async def lesson_finished(self, page: Page, lesson: Lesson) -> bool:
        """当前小节是否播完。

        以 class 上的 active 标记是否已移交给下一节为准（智慧树会自动切集），
        再辅以视频播放到尾作为兜底。
        """
        markers = (
            (self.HIKE_LESSON_ACTIVE,) if self.is_hike else self.LESSON_ACTIVE_MARKERS
        )
        try:
            cls = await lesson.handle.get_attribute("class") or ""
            # 播放位已交给别的小节 => 本节结束
            if cls and not any(m in cls for m in markers):
                return True
        except Exception:
            pass
        try:
            done = await page.evaluate(
                "(() => { const v = document.querySelector('video');"
                " return !!(v && v.duration && v.currentTime >= v.duration - 1.5); })()"
            )
            return bool(done)
        except Exception:
            return False

    # ---------- 答题支线 ----------

    async def detect_question(self, page: Page) -> bool:
        try:
            return await page.query_selector(self.QUESTION_TITLE_SEL) is not None
        except Exception:
            return False

    async def extract_questions(self, page: Page) -> list[Question]:
        """提取弹窗中所有题目。

        智慧树的弹题可能一次弹多道，用 .number 逐题切换，
        每次切换后重新读取当前题干与选项。
        """
        questions: list[Question] = []
        try:
            container = await page.query_selector(self.QUESTION_LIST_SEL)
            numbers = await container.query_selector_all(self.QUESTION_NUMBER_SEL) if container else []
        except Exception:
            numbers = []

        # 只有一道题时页面不渲染题号列表
        if not numbers:
            q = await self._read_current_question(page)
            return [q] if q else []

        for number in numbers:
            try:
                await number.click(timeout=3000)
                await asyncio.sleep(0.4)
            except Exception:
                continue
            q = await self._read_current_question(page)
            if q:
                questions.append(q)
        return questions

    async def _read_current_question(self, page: Page) -> Question | None:
        try:
            title_node = await page.query_selector(self.QUESTION_TITLE_SEL)
            if not title_node:
                return None
            stem = (await title_node.text_content() or "").strip()
            if not stem:
                return None
            option_nodes = await page.query_selector_all(self.OPTION_SEL)
            options: list[Option] = []
            for i, node in enumerate(option_nodes):
                text = (await node.text_content() or "").strip()
                text = " ".join(text.split())
                if not text:
                    continue
                # 选项文本常自带 "A." 前缀，剥掉以免重复
                key = chr(ord("A") + i)
                if len(text) > 2 and text[0].upper().isalpha() and text[1] in ".、．":
                    key = text[0].upper()
                    text = text[2:].strip()
                options.append(Option(key=key, text=text))
            qtype = self._guess_type(stem, options)
            return Question(stem=stem, options=options, qtype=qtype)
        except Exception as exc:
            logger.debug(f"提取题目失败：{Logger.summarize(exc)}")
            return None

    @staticmethod
    def _guess_type(stem: str, options: list[Option]) -> str:
        head = stem[:24]
        if "多选" in head:
            return "multiple"
        if "判断" in head:
            return "judge"
        if not options:
            return "fill"
        texts = {o.text.strip() for o in options}
        if texts <= {"正确", "错误", "对", "错", "√", "×", "A", "B"} and len(options) == 2:
            return "judge"
        return "single"

    async def fill_answer(
        self, page: Page, question: Question, keys: list[str], result: AnswerResult
    ) -> bool:
        if not keys:
            return False
        try:
            option_nodes = await page.query_selector_all(self.OPTION_SEL)
        except Exception:
            return False
        wanted = {k.upper() for k in keys}
        clicked = 0
        for i, node in enumerate(option_nodes):
            key = chr(ord("A") + i)
            if i < len(question.options):
                key = question.options[i].key.upper()
            if key not in wanted:
                continue
            try:
                await node.click(timeout=3000)
                clicked += 1
                await asyncio.sleep(0.2)
            except Exception as exc:
                logger.debug(f"点选选项 {key} 失败：{Logger.summarize(exc)}")
        return clicked > 0

    async def submit_answer(self, page: Page, auto_submit: bool) -> bool:
        if not auto_submit:
            logger.info("已填写答案但未提交（auto_submit = false），可自行确认后提交。")
            return True
        for sel in (".submit-btn", ".btn-submit", "button.submit"):
            try:
                node = await page.query_selector(sel)
                if node:
                    await node.click(timeout=3000)
                    logger.info("答案已提交。")
                    return True
            except Exception:
                continue
        logger.warn("未找到提交按钮，答案保持已填写状态。")
        return False

    async def clear_selection(self, page: Page, question: Question) -> None:
        """取消已选中的选项。

        多选题不清空会越点越多，最后变成"全选"；
        单选题多数平台点新选项会自动换掉旧的，但不保证，所以一并处理。
        """
        try:
            nodes = await page.query_selector_all(self.OPTION_SEL)
        except Exception:
            return
        for node in nodes:
            try:
                cls = await node.get_attribute("class") or ""
                # 只点掉当前处于选中态的，避免把未选的点成选中
                if any(m in cls for m in ("active", "selected", "checked", "on")):
                    await node.click(timeout=2000)
                    await asyncio.sleep(0.12)
            except Exception:
                continue

    # 判定对错的 class 标记。平台改版时这里最容易失效，所以多列几个。
    RIGHT_MARKERS = ("right", "correct", "success", "is-right")
    WRONG_MARKERS = ("wrong", "error", "danger", "is-wrong")
    # 反馈提示所在的容器。不能全页搜文本——判断题的选项本身就叫"正确"，会误判
    FEEDBACK_SEL = ".el-message, .tips, .result-tip, .answer-result, .topic-result"

    async def read_feedback(self, page: Page) -> str:
        """判断平台是否认可本次作答。

        判据按可靠性排序，弹窗消失是最强的信号——
        智慧树答对后会直接收起题目让视频继续播。
        """
        await asyncio.sleep(0.8)  # 给平台一点渲染反馈的时间

        # 1. 题目没了 = 这题过了
        try:
            if not await page.query_selector(self.QUESTION_TITLE_SEL):
                return "correct"
        except Exception:
            return "correct"

        # 2. 选项上的对错标记
        try:
            for node in await page.query_selector_all(self.OPTION_SEL):
                cls = (await node.get_attribute("class") or "").lower()
                if any(m in cls for m in self.WRONG_MARKERS):
                    return "wrong"
                if any(m in cls for m in self.RIGHT_MARKERS):
                    return "correct"
        except Exception:
            pass

        # 3. 限定区域内的文字提示
        try:
            for node in await page.query_selector_all(self.FEEDBACK_SEL):
                text = (await node.text_content() or "").strip()
                if not text:
                    continue
                if any(w in text for w in ("错误", "答错", "不正确", "再想想", "重新")):
                    return "wrong"
                if any(w in text for w in ("正确", "答对", "回答对")):
                    return "correct"
        except Exception:
            pass

        return "unknown"

    async def confirm_and_close(self, page: Page) -> bool:
        """答对后点确认/继续，让视频接着播。"""
        for sel in (".confirm-btn", ".btn-confirm", ".continue-btn",
                    ".el-button--primary", ".know-btn"):
            try:
                node = await page.query_selector(sel)
                if node:
                    await node.click(timeout=2000)
                    await asyncio.sleep(0.4)
                    if not await self.detect_question(page):
                        return True
            except Exception:
                continue
        await self.close_question(page)
        return not await self.detect_question(page)

    async def close_question(self, page: Page) -> None:
        """关闭弹窗。Escape 是智慧树最稳的关闭方式。"""
        for attempt in (
            lambda: page.press(self.DIALOG_SEL, "Escape", timeout=2000),
            lambda: page.evaluate(
                "document.dispatchEvent(new KeyboardEvent('keydown',"
                "{bubbles:true, keyCode:27}));"
            ),
            lambda: page.click(".el-message-box__headerbtn", timeout=2000),
        ):
            try:
                await attempt()
                await asyncio.sleep(0.3)
                if not await self.detect_question(page):
                    return
            except Exception:
                continue

    # ---------- 风控 ----------

    async def detect_captcha(self, page: Page) -> bool:
        try:
            return await page.query_selector(self.CAPTCHA_SEL) is not None
        except Exception:
            return False

    async def captcha_cleared(self, page: Page) -> bool:
        try:
            return await page.query_selector(self.CAPTCHA_SEL) is None
        except Exception:
            return True
