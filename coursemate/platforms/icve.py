"""智慧职教旧版学习空间（zjy2.icve.com.cn）。"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, urlparse

from playwright.async_api import BrowserContext, Page

from ..logger import Logger
from .base import Lesson, PlatformAdapter, register

logger = Logger()


@register
class IcveAdapter(PlatformAdapter):
    name = "智慧职教"
    confirm_catalog_progress = True
    login_url = "https://zjy2.icve.com.cn/study/"
    course_list_url = "https://zjy2.icve.com.cn/study/course"
    index_url = "https://zjy2.icve.com.cn/study/coursePreview/spoccourseIndex"

    def __init__(self) -> None:
        self._paths: dict[str, tuple[int, int, int]] = {}
        self._known_keys: set[str] = set()
        self._known_finished_keys: set[str] = set()
        self._known_titles: dict[str, str] = {}
        self._start_id = ""

    @classmethod
    def match(cls, url: str) -> bool:
        return (urlparse(url).hostname or "").lower() == "zjy2.icve.com.cn"

    async def is_logged_in(self, page: Page) -> bool:
        if (urlparse(page.url).hostname or "") != "zjy2.icve.com.cn":
            return False
        try:
            await page.locator(".navItem .text, h5").filter(has_text="我的课程").first.wait_for(timeout=10000)
            return True
        except Exception:
            return False

    async def _dismiss_wechat_binding(self, page: Page) -> None:
        later = page.get_by_text("下次绑定", exact=True)
        if await later.count() and await later.first.is_visible():
            await later.first.click(timeout=10000)
            logger.info("已选择‘下次绑定’，继续进入智慧职教。")

    async def login(self, page: Page, context: BrowserContext, username: str, password: str) -> None:
        logger.info("正在打开智慧职教登录页...")
        await page.goto(self.login_url, wait_until="domcontentloaded", timeout=30000)
        logger.info("智慧职教登录页已打开。")
        if await self.is_logged_in(page):
            return
        if username and password:
            try:
                tab = page.get_by_text("账号密码登录", exact=True)
                await tab.first.click(timeout=10000)
                logger.info("已切换到账号密码登录。")
                await page.get_by_placeholder("请输入账号").fill(username, timeout=10000)
                await page.get_by_placeholder("请输入密码").fill(password, timeout=10000)
                agreement = page.get_by_role("checkbox").first
                if (await agreement.count() and await agreement.is_visible(timeout=10000)
                        and not await agreement.is_checked(timeout=10000)):
                    await agreement.check(timeout=10000)
                logger.info("账号密码已填写，协议已勾选，正在点击登录。")
                await page.locator(".demo-ruleForm .login").click(timeout=10000)
                logger.info("已提交智慧职教账号密码；如出现滑块验证，请在浏览器手动完成。")
            except Exception as exc:
                logger.warn(f"智慧职教自动登录未完成，请在浏览器继续操作：{Logger.summarize(exc)}")
        else:
            logger.warn("请在浏览器中登录智慧职教并完成滑块验证。")
        captcha_notified = False
        for _ in range(12 * 3600):
            await self._dismiss_wechat_binding(page)
            if await self.is_logged_in(page):
                return
            if not captcha_notified and await self.detect_captcha(page):
                logger.warn("[需要你处理] 智慧职教出现安全验证，请在浏览器手动完成。")
                captcha_notified = True
            await asyncio.sleep(2)

    async def open_course(self, page: Page, url: str) -> str:
        class_id = parse_qs(urlparse(url).query).get("classId", [""])[0]
        self._start_id = parse_qs(urlparse(url).query).get("id", [""])[0]
        if not class_id:
            logger.error("智慧职教地址缺少 classId，请复制视频播放页的完整地址。")
            return "打开失败"
        await page.goto(self.course_list_url, wait_until="domcontentloaded")
        if not await self.is_logged_in(page):
            return "未登录"
        try:
            await page.wait_for_selector(".course .case", timeout=20000)
            for tab in ("标准课程", "快速课程"):
                await page.locator(".el-tabs__item").filter(has_text=tab).click()
                await page.wait_for_load_state("networkidle", timeout=20000)
                cards = await page.evaluate("""() => {
                    const first = document.querySelector('.course .case');
                    const data = first?.closest('.content')?.__vue__?.caselist
                        || document.querySelector('.course')?.__vue__?.caselist || [];
                    return data.map(x => ({classId: x.classId, name: x.courseName}));
                }""")
                index = next((i for i, card in enumerate(cards)
                              if card["classId"] == class_id), None)
                if index is not None:
                    break
            if index is None:
                raise ValueError("classId not found")
        except Exception:
            logger.error("在‘我的课程’当前列表中找不到此班级，请确认该账号已加入课程。")
            return "打开失败"
        await page.locator(".course .case").nth(index).get_by_role("button", name="查看").click()
        await page.wait_for_url("**/spoccourseIndex", timeout=20000)
        await page.wait_for_selector(".listItem", timeout=20000)
        return cards[index]["name"] or "智慧职教课程"

    async def _open_index(self, page: Page) -> None:
        if page.url.split("?")[0] != self.index_url:
            await page.goto(self.index_url, wait_until="domcontentloaded")
        await page.wait_for_selector(".listItem", timeout=20000)

    async def _catalog_items(self, page: Page) -> list[dict]:
        return await page.evaluate("""() => {
            const root = document.querySelector('.listItem');
            const list = root?.parentElement?.parentElement?.parentElement?.__vue__?.list || [];
            return list.flatMap((section, i) => (section.children || []).flatMap((group, j) =>
                (group.children || []).map((item, k) => ({
                    id: item.id, name: item.name, type: item.fileType,
                    speed: Number(item.speed || 0), path: [i, j, k]
                }))));
        }""")

    async def _expand_catalog(self, page: Page, focus_paths=()) -> None:
        """展开目录；缺项重试时优先展开其上次所在的章节。"""
        focus = {(path[0], path[1]) for path in focus_paths if len(path) >= 2}
        roots = page.locator(".listItem")
        for i in range(await roots.count()):
            root = roots.nth(i)
            groups = root.locator(".iChild")
            if not await groups.count() or not await groups.first.is_visible():
                await root.locator(":scope > .items > .ts").click(timeout=5000)
            await root.locator(".iChild").first.wait_for(timeout=10000)

        ordered: list[tuple[int, int]] = []
        for i in range(await roots.count()):
            children = roots.nth(i).locator(".iChild")
            for j in range(await children.count()):
                if (i, j) in focus:
                    ordered.append((i, j))
        for i in range(await roots.count()):
            children = roots.nth(i).locator(".iChild")
            for j in range(await children.count()):
                if (i, j) not in focus:
                    ordered.append((i, j))

        for i, j in ordered:
            child = roots.nth(i).locator(".iChild").nth(j)
            files = child.locator("..").locator(":scope > .fList .fwi")
            if not await files.count() or not await files.first.is_visible():
                await child.locator(".ts").click(timeout=5000)
                await asyncio.sleep(0.3)

    async def _wait_catalog_stable(
        self, page: Page, expected_keys=(), observe_all: bool = False
    ) -> list[dict]:
        """SPA 的 networkidle 不代表 Vue 目录已刷新，稳定后才采纳。"""
        expected = set(expected_keys)
        previous = None
        last: list[dict] = []
        # 第一次没有历史课件 ID 可核对，不能看到两次半份目录就开始刷课。
        # 因此首次完整观察 24 次（约 7 秒）；后续只要已知未完成课件都在，
        # 连续两次一致即可，避免每节课都额外等待。
        required_samples = 24 if observe_all else 2
        for sample in range(24):
            last = await self._catalog_items(page)
            signature = tuple(sorted(
                (item["id"], item["type"], item["speed"], tuple(item["path"]))
                for item in last
            ))
            if (sample + 1 >= required_samples and signature and signature == previous
                    and expected.issubset({item["id"] for item in last})):
                return last
            previous = signature
            await asyncio.sleep(0.3)
        return last

    async def _read_catalog(
        self, page: Page, focus_paths=(), expected_keys=(), observe_all: bool = False
    ) -> list[dict]:
        await self._open_index(page)
        await self._expand_catalog(page, focus_paths)
        return await self._wait_catalog_stable(page, expected_keys, observe_all)

    async def _refresh_catalog(self, page: Page, missing: set[str]) -> None:
        names = "、".join(self._known_titles.get(key, key) for key in sorted(missing))
        logger.warn(f"智慧职教目录未返回《{names}》，重新进入课程目录并展开对应章节。")
        await page.goto(self.index_url, wait_until="domcontentloaded")

    async def list_lessons(self, page: Page) -> list[Lesson]:
        supported = ("video", "ppt", "pdf")
        focus_paths = ()
        observe_all = not self._known_keys
        expected_keys = self._known_keys - self._known_finished_keys
        for attempt in range(3):
            data = await self._read_catalog(page, focus_paths, expected_keys, observe_all)
            keys = {item["id"] for item in data if item["type"] in supported}
            missing = self._known_keys - keys
            unconfirmed_missing = missing - self._known_finished_keys
            if not unconfirmed_missing:
                if missing:
                    logger.debug(
                        f"智慧职教目录本次未返回 {len(missing)} 节已确认完成课件，"
                        "继续核对其余课件。"
                    )
                break
            if attempt == 2:
                titles = "、".join(
                    self._known_titles.get(key, key) for key in sorted(unconfirmed_missing)
                )
                raise RuntimeError(
                    f"智慧职教目录本次少了 {len(unconfirmed_missing)} 节未确认完成课件"
                    f"（{titles}），停止使用不完整目录。"
                )
            logger.warn(
                f"智慧职教目录本次少了 {len(unconfirmed_missing)} 节未确认完成课件，"
                "重新进入目录核对以防漏刷。"
            )
            focus_paths = tuple(
                self._paths[key] for key in unconfirmed_missing if key in self._paths
            )
            expected_keys = self._known_keys - self._known_finished_keys
            await self._refresh_catalog(page, unconfirmed_missing)
        self._known_keys.update(keys)
        self._known_finished_keys.update(
            item["id"] for item in data
            if item["type"] in supported and item["speed"] >= 100
        )
        self._known_titles.update(
            {item["id"]: item["name"] for item in data if item["type"] in supported}
        )
        self._paths = {item["id"]: tuple(item["path"]) for item in data}
        lessons = [Lesson(item["name"], item["id"], item["speed"] >= 100,
                          item["id"], item["type"])
                   for item in data if item["type"] in supported]
        for i, lesson in enumerate(lessons):
            if lesson.key == self._start_id and lesson.finished:
                self._start_id = next(
                    (item.key for item in lessons[i + 1:] if not item.finished),
                    self._start_id,
                )
                break
        return lessons

    async def confirm_lesson_completion(self, page: Page, lesson: Lesson) -> bool:
        for _ in range(3):
            await asyncio.sleep(2)
            try:
                listed = await self.list_lessons(page)
            except Exception as exc:
                logger.warn(f"智慧职教目录进度核对失败：{Logger.summarize(exc)}")
                return False
            current = next((item for item in listed if item.key == lesson.key), None)
            if current is not None and current.finished:
                return True
        return False

    async def enter_lesson(self, page: Page, lesson: Lesson) -> bool:
        path = self._paths.get(lesson.key)
        if path is None:
            return False
        await self._open_index(page)
        i, j, k = path
        leaf = page.locator(".listItem").nth(i).locator(":scope > div").nth(j + 1)
        await leaf.locator(".fList .fwi").nth(k).click()
        await page.wait_for_url(f"**/courseware?id={lesson.key}&**", timeout=20000)
        if lesson.kind in ("ppt", "pdf"):
            try:
                if lesson.kind == "ppt":
                    await page.locator(".FilePreview .page").wait_for(timeout=20000)
                else:
                    await page.get_by_text(re.compile(r"^\s*\d+\s*/\s*\d+\s*$")) \
                        .first.wait_for(timeout=20000)
                await self._dismiss_resume(page)
                return True
            except Exception:
                return False
        try:
            await page.wait_for_selector("video", timeout=15000)
        except Exception:
            return False
        await self._dismiss_resume(page)
        try:
            await page.wait_for_function(
                "() => (document.querySelector('video')?.readyState || 0) >= 2",
                timeout=30000,
            )
            if await page.locator("video").evaluate("v => v.paused"):
                await page.locator(".prism-play-btn").click(timeout=5000)
        except Exception as exc:
            logger.warn(f"智慧职教播放器未能自动启动：{Logger.summarize(exc)}")
        return True

    async def read_document(self, page: Page, lesson: Lesson, should_stop) -> bool:
        await self._dismiss_resume(page)
        pager = (page.locator(".FilePreview .page") if lesson.kind == "ppt" else
                 page.get_by_text(re.compile(r"^\s*\d+\s*/\s*\d+\s*$")).first)

        async def position() -> tuple[int, int]:
            text = await pager.inner_text(timeout=10000)
            match = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if not match:
                raise ValueError("未找到文档页码")
            return int(match.group(1)), int(match.group(2))

        def control_scopes():
            if lesson.kind == "ppt":
                return (pager, page.locator(".FilePreview").first, page)
            parent = pager.locator("xpath=..")
            return (parent, parent.locator("xpath=.."), page)

        async def click_turn(name: str) -> bool:
            arrow = ".el-icon-arrow-right" if name == "下一页" else ".el-icon-arrow-left"
            for scope in control_scopes():
                candidates = (
                    scope.get_by_role("button", name=name, exact=True),
                    scope.get_by_text(name, exact=True),
                    scope.locator(f'[aria-label="{name}"], [title="{name}"]'),
                    scope.locator(
                        f"button:has({arrow}), [role='button']:has({arrow}), a:has({arrow})"
                    ),
                    scope.locator(arrow).locator("xpath=.."),
                )
                for candidate in candidates:
                    if not await candidate.count() or not await candidate.first.is_visible():
                        continue
                    try:
                        await candidate.first.click(timeout=3000)
                        return True
                    except Exception:
                        continue
            return False

        async def turn(name: str, target: int) -> None:
            for _ in range(2):
                before, _ = await position()
                if before == target:
                    return
                if await click_turn(name):
                    for _ in range(60):
                        if should_stop():
                            return
                        try:
                            current, _ = await position()
                        except Exception:
                            await asyncio.sleep(0.25)
                            continue
                        if current == target:
                            return
                        if current != before:
                            raise TimeoutError(f"文档翻页后到了意外页码 {current}")
                        await asyncio.sleep(0.25)
                if should_stop():
                    return
                await asyncio.sleep(1)
            raise TimeoutError(f"未能点击或确认文档{name}到第 {target} 页")

        try:
            current, total = await position()
            if total < 1:
                return False
            while current > 1:
                if should_stop():
                    return False
                await turn("上一页", current - 1)
                current -= 1
            if should_stop():
                return False
            await asyncio.sleep(1)
            while current < total:
                if should_stop():
                    return False
                await turn("下一页", current + 1)
                current += 1
                logger.progress(f"{lesson.title[:20]} 页数:", f"{current}/{total}")
                await asyncio.sleep(1)
            for _ in range(12):
                if should_stop():
                    return False
                await asyncio.sleep(0.5)
            return True
        except Exception as exc:
            logger.warn(f"智慧职教文档翻页未完成：{Logger.summarize(exc)}")
            return False

    async def _dismiss_resume(self, page: Page) -> None:
        box = page.locator(".el-message-box").filter(has_text="上次观看到")
        if await box.count() and await box.first.is_visible():
            confirm = box.first.get_by_role("button", name="确定", exact=True)
            if await confirm.count():
                await confirm.first.click()
            else:
                await box.first.locator("button.el-button--primary").click()
            try:
                await box.first.wait_for(state="hidden", timeout=15000)
            except Exception:
                logger.warn("智慧职教续播提示未关闭，请在浏览器中确认。")

    async def get_progress(self, page: Page) -> str:
        return await page.evaluate("""() => {
            const v = document.querySelector('video');
            return v && Number.isFinite(v.duration) && v.duration > 0
                ? `${Math.floor(v.currentTime / v.duration * 100)}%` : '';
        }""")

    async def lesson_finished(self, page: Page, lesson: Lesson) -> bool:
        await self._dismiss_resume(page)
        return await page.evaluate("() => !!document.querySelector('video')?.ended")

    async def active_lesson_key(self, page: Page) -> str:
        return parse_qs(urlparse(page.url).query).get("id", [self._start_id])[0]

    async def detect_captcha(self, page: Page) -> bool:
        if (urlparse(page.url).hostname or "") != "sso.icve.com.cn":
            return False
        frames = page.locator('iframe[src*="captcha" i], iframe[src*="aliyun" i]')
        if any([await frames.nth(i).is_visible() for i in range(await frames.count())]):
            return True
        text = await page.inner_text("body", timeout=5000)
        return any(hint in text for hint in ("拖动拼图", "请完成安全验证", "向右滑动完成验证"))

    async def captcha_cleared(self, page: Page) -> bool:
        return not await self.detect_captcha(page)
