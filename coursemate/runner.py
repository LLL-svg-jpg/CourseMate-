"""刷课主循环。

职责边界很清楚：
- 主循环只做一件事——推进章节，让进度往前走
- 播放不中断、题目被处理、验证被发现，全交给 workers 里的常驻协程

这样拆分的好处是，弹题、验证码这类打断不需要主循环去感知，
它只管盯着"这一节播完了没有"。
"""
from __future__ import annotations

import asyncio

from playwright.async_api import BrowserContext, Page, async_playwright

from .answer.ai import build_provider
from .answer.cache import AnswerCache
from .browser import launch, persist_login, clear_cookies
from .config import Config
from .diagnostics import dump_page_structure
from .events import StudyClock
from .logger import Logger
from .platforms import resolve, supported_platforms
from .platforms.base import Lesson, PlatformAdapter
from .workers import (
    captcha_worker,
    is_closed,
    playback_worker,
    question_worker,
    task_monitor,
    tuning_worker,
)

logger = Logger()

# 单节最长等待，防止某节因平台异常永久卡住拖死整个流程
LESSON_TIMEOUT_SECONDS = 3 * 3600


class StopRequested(Exception):
    """用户主动请求停止。从任意深度抛出，由 run() 统一收敛。"""


def _noop_stop() -> bool:
    return False


async def ensure_login(
    page: Page, context: BrowserContext, adapter: PlatformAdapter, config: Config
) -> None:
    if await adapter.is_logged_in(page):
        logger.info("登录状态有效。")
        return
    logger.info("需要登录，正在处理...")
    await adapter.login(page, context, config.username, config.password)
    logger.info("登录完成。", shift=True)
    await persist_login(context)


async def study_lesson(
    page: Page, adapter: PlatformAdapter, lesson: Lesson, clock: StudyClock,
    config: Config, should_stop=_noop_stop
) -> str:
    """播完一节。返回结束原因：finished / timeout / limit / skipped。"""
    if not await adapter.enter_lesson(page, lesson):
        return "skipped"

    logger.info(f"正在学习：{lesson.title}")
    waited = 0.0
    while True:
        if should_stop():
            raise StopRequested
        if config.limit_max_minutes and clock.reached(config.limit_max_minutes):
            return "limit"
        if waited > LESSON_TIMEOUT_SECONDS:
            logger.warn("本节等待超时，跳到下一节。", shift=True)
            return "timeout"
        try:
            if await adapter.lesson_finished(page, lesson):
                return "finished"
            progress = await adapter.get_progress(page)
            if progress:
                logger.progress(f"{lesson.title[:20]} 进度:", progress)
        except Exception as exc:
            if is_closed(exc):
                raise
            logger.debug(f"进度轮询未命中：{Logger.summarize(exc)}")
        # 切成小段睡眠，让"停止"最多 0.5 秒内生效
        for _ in range(4):
            if should_stop():
                raise StopRequested
            await asyncio.sleep(0.5)
        waited += 2


async def study_course(
    page: Page, adapter: PlatformAdapter, url: str, clock: StudyClock,
    config: Config, should_stop=_noop_stop
) -> bool:
    """学习一门课程。返回是否真的进入了学习流程。"""
    title = await adapter.open_course(page, url)
    logger.info(f"当前课程：《{title}》", shift=True)

    # open_course 用这两个特殊返回值表示"根本没进对页面"，
    # 此时再去找章节只会得到一句误导性的"读不到章节列表"
    if title in ("未登录", "打开失败"):
        logger.error("没能正常进入课程页，本课程跳过。")
        await dump_page_structure(page, (), "open_course")
        return False

    await adapter.prepare_page(page)

    lessons = await adapter.list_lessons(page)
    if not lessons:
        logger.warn("未能读取到章节列表。", shift=True)
        # 光说"读不到"没法排查，把页面真实结构导出来，问题才从猜变成看
        candidates = tuple(getattr(adapter, "LESSON_CANDIDATES", ()) or ())
        dump = await dump_page_structure(page, candidates, "lessons")
        if dump:
            logger.error(f"已导出页面结构：{dump}")
            logger.error("把这个文件发给作者，就能补上对应的适配。")
        logger.info("常见原因：① 地址不是视频播放页 ② 登录未生效 ③ 平台改版。")
        return False

    pending = [ls for ls in lessons if not ls.finished]
    logger.info(f"共 {len(lessons)} 节，其中 {len(pending)} 节待完成。")
    targets = pending or lessons
    if not pending:
        logger.info("所有小节均已标记完成，将按复习模式重新过一遍。")

    for index, lesson in enumerate(targets, 1):
        logger.info(f"[{index}/{len(targets)}] {lesson.title}", shift=True)
        reason = await study_lesson(page, adapter, lesson, clock, config, should_stop)
        print()  # 结束 progress 的原地刷新行
        if reason == "limit":
            logger.info(
                f"已达设定时长上限 {config.limit_max_minutes} 分钟，停止本课程。", shift=True
            )
            return True
        if reason == "finished":
            logger.info(f"《{lesson.title}》完成。")
        elif reason == "skipped":
            logger.info("非视频任务点，已跳过。")

    logger.info(f"《{title}》全部小节处理完毕。", shift=True)
    return True


async def run(config: Config, should_stop=_noop_stop) -> None:
    urls = config.course_urls
    unresolved = [u for u in urls if resolve(u) is None]
    if unresolved:
        logger.error(f"以下 URL 没有匹配的平台适配器：{unresolved}")
        logger.info(f"当前支持：{', '.join(supported_platforms())}")
        urls = [u for u in urls if resolve(u) is not None]
    if not urls:
        return

    cache = AnswerCache(enabled=config.answer_cache)
    if config.answer_cache:
        total, hits = cache.stats()
        logger.info(f"本地题库已收录 {total} 道题，累计命中 {hits} 次。")

    provider = build_provider(config)
    if provider:
        logger.info(f"AI 作答已启用：{provider.name} / {config.model}")
    elif config.answer_enabled:
        logger.info("未启用 AI，遇题将按选项顺序逐个尝试作答。")
    if config.retry_until_correct:
        logger.info("答题模式：答错自动换答案重试，直到平台判定正确。")
    else:
        logger.info("答题模式：只作答一次" + ("并提交。" if config.auto_submit else "，不提交。"))
    clock = StudyClock()
    tasks: list[asyncio.Task] = []
    studied_any = False

    async with async_playwright() as p:
        page, context = await launch(p, config)
        try:
            first_adapter = resolve(urls[0])
            assert first_adapter is not None
            await ensure_login(page, context, first_adapter, config)

            # 常驻协程在整个会话期间只启动一次
            tasks = [
                asyncio.create_task(playback_worker(page, first_adapter), name="playback"),
                asyncio.create_task(tuning_worker(page, first_adapter, config), name="tuning"),
                asyncio.create_task(
                    captcha_worker(page, first_adapter, config, clock), name="captcha"
                ),
                asyncio.create_task(
                    question_worker(page, first_adapter, config, clock, provider, cache),
                    name="question",
                ),
            ]
            monitor = asyncio.create_task(task_monitor(tasks), name="monitor")
            tasks.append(monitor)

            for url in urls:
                if should_stop():
                    raise StopRequested
                adapter = resolve(url)
                if adapter is None:
                    continue
                logger.info("=" * 46, shift=True)
                clock.reset()
                if not await adapter.is_logged_in(page):
                    logger.warn("登录状态已失效，正在重新登录。", shift=True)
                    clear_cookies()
                    await ensure_login(page, context, adapter, config)
                if await study_course(page, adapter, url, clock, config, should_stop):
                    studied_any = True
                logger.info(
                    f"本课程有效学习 {clock.elapsed_minutes:.1f} 分钟"
                    f"（另有 {clock.paused_minutes:.1f} 分钟为答题/验证等待，未计入）。"
                )

            logger.info("=" * 46, shift=True)
            if studied_any:
                logger.info("全部课程处理完毕。")
            if config.answer_cache:
                total, hits = cache.stats()
                logger.info(f"本地题库现有 {total} 道题，累计命中 {hits} 次。")

            # 一节都没学成就直接关浏览器，用户只会看到"窗口一闪就没了"，
            # 既看不到出错页面也没法手动接管。留着窗口，由用户点停止再关。
            if not studied_any and config.keep_browser_open:
                logger.warn("没有成功学习任何内容，浏览器先不关闭。", shift=True)
                logger.warn("你可以在浏览器里手动看看是哪一步不对；"
                            "看完点界面上的「停止」按钮即可关闭。")
                while not should_stop():
                    await asyncio.sleep(1)
        except StopRequested:
            logger.info("已按你的要求停止。", shift=True)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if provider:
                await provider.aclose()
            cache.close()
