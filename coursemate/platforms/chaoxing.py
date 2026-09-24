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
import base64

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
    COMPLETED_SEL = ".icon_Completed, .icon_yiwanc"
    # 超星的滑块/验证弹层
    CAPTCHA_SELECTORS = ("#nc_1_wrapper", ".nc-container", ".geetest_panel")
    # 视频内弹题
    QUIZ_TITLE_SEL = ".ans-videoquiz-title, .videoquiz-title"
    QUIZ_OPTION_SEL = ".ans-videoquiz-opt, .videoquiz-opt"
    QUIZ_SUBMIT_SEL = ".ans-videoquiz-submit, .videoquiz-submit"
    CHAPTER_TEST_SEL = '.TiMu input[name^="answertype"], .Zy_TItle'
    CHAPTER_TAB_SEL = '#prev_tab li[title="章节测验"]'
    CHAPTER_QUESTION_SEL = ".TiMu.newTiMu"

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
                node = await frame.query_selector(self.QUIZ_TITLE_SEL)
                if node and await node.is_visible():
                    return frame
            except Exception:
                continue
        return None

    async def _chapter_test_visible(self, page: Page) -> bool:
        """当前任务点是否是章节测试，而不是视频内弹题。"""
        return await self._chapter_test_frame(page) is not None

    async def _chapter_test_frame(self, page: Page) -> Page | Frame | None:
        """定位独立章节测验最内层 frame。"""
        for frame in page.frames:
            try:
                node = await frame.query_selector(self.CHAPTER_TEST_SEL)
                if node and await node.is_visible():
                    return frame
            except Exception:
                continue
        return None

    async def _skip_replayed_tail(self, frame: Page | Frame) -> float:
        """跳过重新打开时平台回退播放的最后约一分钟。"""
        skipped = await frame.evaluate(
            """() => {
                const v = document.querySelector('video');
                if (!v || !Number.isFinite(v.duration) || v.duration <= 0) return 0;
                const key = `${v.currentSrc}|${v.duration}`;
                if (v.__coursemateTailChecked === key) return 0;
                v.__coursemateTailChecked = key;

                const remaining = v.duration - v.currentTime;
                // 新视频从 0 开始时绝不跳；只处理打开即落在结尾 75 秒内的恢复播放。
                if (v.currentTime < 15 || remaining <= 2 || remaining > 75 || !v.seekable.length) {
                    return 0;
                }
                const target = Math.min(v.duration - 1.5, v.seekable.end(v.seekable.length - 1) - 0.5);
                if (target <= v.currentTime + 2) return 0;
                const oldTime = v.currentTime;
                v.currentTime = target;
                return target - oldTime;
            }"""
        )
        return max(0.0, float(skipped or 0))

    # ---------- 刷课主线 ----------

    async def is_logged_in(self, page: Page) -> bool:
        url = (page.url or "").lower()
        # 新开的 about:blank 过去会被误判成已登录，导致账号密码填写根本不执行。
        if not url.startswith(("http://", "https://")):
            return False
        if "login" in url or "passport" in url:
            return False
        try:
            return "用户登录" not in (await page.title())
        except Exception:
            return True

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
                # 不同学校的登录页有 input 复选框或自绘协议框两种版本。
                for sel in (
                    '#passportAgreement input[type="checkbox"]',
                    'input[type="checkbox"][name*="agree"]',
                    'input[type="checkbox"][id*="agree"]',
                    '.agreement input[type="checkbox"]',
                ):
                    box = page.locator(sel).first
                    if await box.count() and await box.is_visible() and not await box.is_checked():
                        await box.check()
                        break
                await page.locator("#loginBtn").click()
                await page.wait_for_timeout(600)
                # 有些版本会在登录按钮后再弹一次协议确认/“进入”按钮。
                await page.evaluate(
                    """() => {
                        const wanted = /^(同意并登录|同意并继续|确认并登录|进入)$/;
                        const node = [...document.querySelectorAll('button,a')].find(el => {
                            const r = el.getBoundingClientRect();
                            return r.width && r.height && wanted.test((el.innerText || '').trim());
                        });
                        if (node) node.click();
                    }"""
                )
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
        handles = page.locator(self.CATALOG_SEL)
        lessons: list[Lesson] = []
        for index in range(await handles.count()):
            # Locator 会在每次操作时重新解析 DOM。学习通播完一节会重绘目录，
            # 若保存 ElementHandle，第二节开始就可能因节点失效而全部点击失败。
            handle = handles.nth(index)
            try:
                item_id = await handle.get_attribute("id") or ""
                item_class = await handle.get_attribute("class") or ""
                # firstLayer / 无 cur 的项只是“第1章”这类目录标题，不是视频。
                if "firstLayer" in item_class or (item_id and "cur" not in item_id):
                    continue
                name_node = handle.locator(self.CATALOG_NAME_SEL).first
                if await name_node.count():
                    title = ((await name_node.text_content()) or "").strip()
                else:
                    title = ((await handle.text_content()) or "").strip()
                title = " ".join(title.split())[:60]
                finished = await handle.locator(self.COMPLETED_SEL).count() > 0
                pending = handle.locator(".jobUnfinishCount").first
                if await pending.count():
                    value = (await pending.get_attribute("value") or "").strip()
                    finished = value == "0"
                lessons.append(Lesson(
                    title=title or "未命名章节", handle=handle,
                    finished=finished, key=item_id,
                ))
            except Exception:
                continue
        return lessons

    async def enter_lesson(self, page: Page, lesson: Lesson) -> bool:
        # 用户已经手动切到这一节时保留当前任务页，不能再点目录把它拉回视频页。
        if not lesson.key or await self.active_lesson_key(page) != lesson.key:
            try:
                await lesson.handle.click(timeout=10000)
            except Exception as exc:
                logger.warn(f"点击章节失败：{Logger.summarize(exc)}")
                return False
            await page.wait_for_timeout(2500)
            await self.prepare_page(page)

        if await self.detect_chapter_test(page):
            return True
        if await self.video_task_completed(page):
            return True

        # 等待 video 在任意 frame 中出现
        for _ in range(20):
            if await self.video_task_completed(page):
                return True
            frame = await self.video_frame(page)
            if frame is not page:
                skipped = await self._skip_replayed_tail(frame)
                if skipped:
                    logger.info(f"已跳过重新打开后重复的 {skipped:.0f} 秒尾段。")
                return True
            try:
                if await page.query_selector("video"):
                    skipped = await self._skip_replayed_tail(page)
                    if skipped:
                        logger.info(f"已跳过重新打开后重复的 {skipped:.0f} 秒尾段。")
                    return True
            except Exception:
                pass
            if await self._chapter_test_visible(page):
                return True
            await asyncio.sleep(1)
        logger.warn("本章节未找到视频，可能是文档/测验类任务点，跳过。")
        return False

    async def active_lesson_key(self, page: Page) -> str:
        try:
            node = page.locator(f"{self.CATALOG_SEL}.posCatalog_active").first
            return (await node.get_attribute("id") or "") if await node.count() else ""
        except Exception:
            return ""

    async def detect_chapter_test(self, page: Page) -> bool:
        return await self._chapter_test_frame(page) is not None

    async def open_chapter_test(self, page: Page) -> bool:
        try:
            tab = page.locator(self.CHAPTER_TAB_SEL).first
            if not await tab.count() or not await tab.is_visible():
                return False
            await tab.click(timeout=5000)
            for _ in range(20):
                if await self.detect_chapter_test(page):
                    return True
                await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug(f"打开章节测验失败：{Logger.summarize(exc)}")
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
        # 目录的未完成数可能只剩章节测验；当前视频的“任务点已完成”更具体。
        if await self.video_task_completed(page):
            return True
        try:
            if await lesson.handle.locator(self.COMPLETED_SEL).count() > 0:
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

    async def video_task_completed(self, page: Page) -> bool:
        for frame in getattr(page, "frames", []):
            try:
                if await frame.locator(".ans-attach-ct.ans-job-finished").first.is_visible():
                    return True
            except Exception:
                continue
        try:
            return await page.get_by_text("任务点已完成", exact=True).first.is_visible()
        except Exception:
            return False

    # ---------- 答题支线 ----------

    async def detect_question(self, page: Page) -> bool:
        # 这里只认视频内弹题。独立章节测验由主流程单独处理，绝不套用暴力试错。
        return await self._quiz_frame(page) is not None

    async def extract_questions(self, page: Page) -> list[Question]:
        frame = await self._quiz_frame(page)
        if frame is None:
            return await self._extract_chapter_questions(page)
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

    async def _extract_chapter_questions(self, page: Page) -> list[Question]:
        """读取独立章节测验，并截取浏览器真实渲染内容供视觉模型识别。"""
        frame = await self._chapter_test_frame(page)
        if frame is None:
            return []
        questions: list[Question] = []
        rows = frame.locator(self.CHAPTER_QUESTION_SEL)
        for index in range(await rows.count()):
            row = rows.nth(index)
            try:
                title = row.locator(".Zy_TItle").first
                stem = " ".join(((await title.text_content()) or "").split()).strip()
                options: list[Option] = []
                selected_keys: list[str] = []
                option_rows = row.locator("li[qid]")
                answer_field = row.locator('input[name^="answer"]:not([name^="answertype"])').first
                saved_answer = await answer_field.input_value() if await answer_field.count() else None
                for option_index in range(await option_rows.count()):
                    option = option_rows.nth(option_index)
                    text_node = option.locator(".after").first
                    text = " ".join(((await text_node.text_content()) or "").split()).strip()
                    key = chr(ord("A") + option_index)
                    options.append(Option(key, text))
                    marker = option.locator(".num_option, .num_option_dx").first
                    value = await marker.get_attribute("data") if await marker.count() else None
                    if saved_answer is not None:
                        selected = bool(value and value in saved_answer)
                    else:
                        selected = await self._chapter_option_selected(option)
                    if selected:
                        selected_keys.append(key)

                kind = (await row.get_attribute("data") or "").strip()
                qtype = {"0": "single", "1": "multiple", "3": "judge"}.get(kind, "unknown")
                image_data_url = ""
                try:
                    png = await row.screenshot(type="png")
                    image_data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                except Exception as exc:
                    logger.debug(f"截取第 {index + 1} 道章节题失败：{Logger.summarize(exc)}")
                questions.append(Question(
                    stem=stem or f"章节测验第 {index + 1} 题",
                    options=options, qtype=qtype, context="chapter",
                    index=index, image_data_url=image_data_url,
                    selected_keys=selected_keys,
                ))
            except Exception as exc:
                logger.debug(f"提取第 {index + 1} 道章节题失败：{Logger.summarize(exc)}")
        return questions

    @staticmethod
    async def _chapter_option_selected(option) -> bool:
        """读取学习通当前真实选择状态，兼容原生 input 和自绘选项。"""
        try:
            if await option.get_attribute("aria-checked") == "true":
                return True
            inputs = option.locator('input[type="radio"], input[type="checkbox"]')
            for index in range(await inputs.count()):
                if await inputs.nth(index).is_checked():
                    return True
            marker = option.locator(
                'input:checked, [aria-checked="true"], .check_answer, '
                '.check_answer_dx, .checked, .selected'
            ).first
            return bool(await marker.count())
        except Exception:
            return False

    async def _chapter_answered_count(self, frame: Page | Frame) -> tuple[int, int]:
        rows = frame.locator(self.CHAPTER_QUESTION_SEL)
        count = await rows.count()
        answered = 0
        for row_index in range(count):
            row = rows.nth(row_index)
            field = row.locator('input[name^="answer"]:not([name^="answertype"])').first
            if await field.count():
                has_answer = bool((await field.input_value()).strip())
            else:
                options = row.locator("li[qid]")
                has_answer = any([
                    await self._chapter_option_selected(options.nth(option_index))
                    for option_index in range(await options.count())
                ])
            if has_answer:
                answered += 1
        return answered, count

    async def fill_answer(
        self, page: Page, question: Question, keys: list[str], result: AnswerResult
    ) -> bool:
        if question.context == "chapter":
            frame = await self._chapter_test_frame(page)
            if frame is None or question.index < 0 or not keys:
                return False
            wanted = {k.upper() for k in keys}
            row = frame.locator(self.CHAPTER_QUESTION_SEL).nth(question.index)
            options = row.locator("li[qid]")
            field = row.locator('input[name^="answer"]:not([name^="answertype"])').first
            # 提取后到填写前用户也可能手动作答；一旦已有选择就不再触碰。
            if await field.count() and (await field.input_value()).strip():
                logger.info(f"第 {question.index + 1} 题已有答案，保留原选择。")
                return False
            for index in range(await options.count()):
                if await self._chapter_option_selected(options.nth(index)):
                    logger.info(f"第 {question.index + 1} 题已有答案，保留原选择。")
                    return False
            clicked = 0
            for index in range(await options.count()):
                if chr(ord("A") + index) not in wanted:
                    continue
                try:
                    await options.nth(index).click(timeout=3000)
                    clicked += 1
                    await asyncio.sleep(0.15)
                except Exception as exc:
                    logger.debug(f"第 {question.index + 1} 题点选 {index + 1} 失败：{Logger.summarize(exc)}")
                    continue
            if clicked != len(wanted):
                logger.warn(f"第 {question.index + 1} 题仅点选 {clicked}/{len(wanted)} 个选项。")
                return False
            selected = {
                chr(ord("A") + index)
                for index in range(await options.count())
                if await self._chapter_option_selected(options.nth(index))
            }
            if selected != wanted or (await field.count() and not (await field.input_value()).strip()):
                logger.warn(
                    f"第 {question.index + 1} 题选择未生效：预期 {sorted(wanted)}，"
                    f"页面显示 {sorted(selected)}。"
                )
                return False
            return True

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
        chapter = await self._chapter_test_frame(page)
        if chapter is not None:
            try:
                async def submitted() -> bool:
                    active = page.locator(f"{self.CATALOG_SEL}.posCatalog_active .jobUnfinishCount").first
                    if await active.count() and await active.get_attribute("value") == "0":
                        return True
                    current = await self._chapter_test_frame(page)
                    return current is not None and bool(await current.locator(
                        ".TiMu.newTiMu.ans-cc .newAnswerBx"
                    ).count())

                before_answered, before_total = await self._chapter_answered_count(chapter)
                if not before_answered:
                    logger.warn("章节测验没有可保存的答案，已取消保存/提交。")
                    return False
                if auto_submit and before_answered != before_total:
                    logger.warn("章节测验仍有空题，只能暂存，不能正式提交。")
                    return False
                if not auto_submit:
                    save = chapter.locator(".btnSave").first
                    if not await save.count() or not await save.is_visible():
                        logger.warn("章节测验未找到可用的暂存按钮。")
                        return False
                    origin = await chapter.evaluate("performance.timeOrigin")
                    await save.click(timeout=5000)
                    # 学习通保存成功后会刷新测验 frame；刷新后再读答案才算持久化。
                    for _ in range(20):
                        await asyncio.sleep(0.5)
                        current = await self._chapter_test_frame(page)
                        if current is None:
                            continue
                        try:
                            if await current.evaluate("performance.timeOrigin") == origin:
                                continue
                            after_answered, _ = await self._chapter_answered_count(current)
                            if after_answered >= before_answered:
                                logger.info("章节测验已暂时保存，重新载入后答案仍在。")
                                return True
                            break
                        except Exception:
                            continue
                    logger.warn("章节测验暂存后未能确认答案已持久化。")
                    return False

                # 此版学习通先暂存并刷新测验，再正式提交；避免未落盘答案在提交校验时丢失。
                save = chapter.locator(".btnSave").first
                if await save.count() and await save.is_visible():
                    if not await self.submit_answer(page, auto_submit=False):
                        return False
                    chapter = await self._chapter_test_frame(page)
                    if chapter is None or await self._chapter_answered_count(chapter) != (
                        before_answered, before_total
                    ):
                        logger.warn("章节测验暂存后答案未完整保留，已取消正式提交。")
                        return False

                button = chapter.locator(".btnSubmit").first
                if not await button.count() or not await button.is_visible():
                    logger.warn("章节测验未找到可用的提交按钮。")
                    return False
                confirmed = False
                validation_status = None
                async def on_dialog(dialog) -> None:
                    nonlocal confirmed
                    if dialog.type == "confirm" and "提交" in dialog.message:
                        await dialog.accept()
                        confirmed = True
                    else:
                        logger.warn(f"章节测验提交时平台提示：{dialog.message[:120]}")
                        if dialog.type == "alert":
                            await dialog.accept()
                        else:
                            await dialog.dismiss()

                async def on_response(response) -> None:
                    nonlocal validation_status
                    if "/work/validate" not in response.url:
                        return
                    try:
                        validation_status = (await response.json()).get("status")
                    except Exception:
                        validation_status = "无法读取"

                # 学习通有页面弹层和浏览器原生 confirm 两种确认方式。
                # Playwright 未监听原生对话框时会自动取消，表现为“点了提交却只暂存”。
                page.on("dialog", on_dialog)
                page.on("response", on_response)
                try:
                    await button.click(timeout=5000)
                    for _ in range(20):
                        if confirmed or await submitted():
                            break
                        popup = page.locator("#workpop").first
                        if await popup.is_visible():
                            prompt = (await popup.locator("#popcontent").inner_text()).strip()
                            ok = popup.locator("#popok").first
                            if "提交" in prompt and (await ok.inner_text()).strip() in (
                                "提交", "确定", "确认", "确认提交", "确定提交"
                            ):
                                await ok.click(timeout=3000)
                                confirmed = True
                                break
                        for frame in page.frames:
                            try:
                                for selector in (".layui-layer:visible",
                                                 '[role="dialog"]:visible', ".modal:visible"):
                                    popup = frame.locator(selector).first
                                    if not await popup.count() or "提交" not in await popup.inner_text():
                                        continue
                                    controls = popup.locator("#popok, .layui-layer-btn0, button, a")
                                    for index in range(await controls.count()):
                                        control = controls.nth(index)
                                        if (await control.inner_text()).strip() in (
                                            "提交", "确定", "确认", "确认提交", "确定提交"
                                        ):
                                            await control.click(timeout=3000)
                                            confirmed = True
                                            break
                                    if confirmed:
                                        break
                            except Exception:
                                continue
                            if confirmed:
                                break
                        if confirmed:
                            break
                        await asyncio.sleep(0.5)
                finally:
                    page.remove_listener("dialog", on_dialog)
                    page.remove_listener("response", on_response)
                if not confirmed:
                    if await submitted():
                        logger.info("章节测验已提交。")
                        return True
                    if str(validation_status) == "3":
                        # 平台已允许提交，但偶发未显示确认弹层；调用弹层原本的确认回调。
                        confirmed = await chapter.evaluate(
                            "() => { if (typeof submitCheckTimes !== 'function') return false;"
                            " submitCheckTimes(); return true; }"
                        )
                        if confirmed:
                            logger.info("章节测验校验通过，已执行平台提交确认。")
                    if confirmed:
                        for _ in range(30):
                            await asyncio.sleep(0.5)
                            if await submitted():
                                logger.info("章节测验已提交。")
                                return True
                    logger.warn(
                        "未找到章节测验的“提交”确认按钮，尚未确认正式提交；"
                        f"平台校验状态：{validation_status if validation_status is not None else '未收到校验响应'}。"
                    )
                    return False

                # 等结果页或目录任务数归零；短暂的 frame 重载不能算提交成功。
                for _ in range(30):
                    await asyncio.sleep(0.5)
                    if await submitted():
                        logger.info("章节测验已提交。")
                        return True
                logger.warn("已点击提交，但平台未返回完成标记或结果页。")
                return False
            except Exception as exc:
                logger.warn(f"章节测验保存/提交失败：{Logger.summarize(exc)}")
                return False

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
