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
from .answer.base import AnswerProvider
from .answer.cache import AnswerCache
from .browser import launch, persist_login
from .config import Config
from .diagnostics import dump_page_structure
from .events import StudyClock
from .exam import is_exam_url, study_exam
from .logger import Logger
from .platforms import resolve, supported_platforms
from .platforms.base import Lesson, PlatformAdapter
from .workers import (
    captcha_worker,
    is_closed,
    playback_worker,
    question_worker,
    solve_chapter_test_once,
    task_monitor,
    tuning_worker,
)
from .zhihuishu_work import is_work_url, study_work

logger = Logger()

# 单节最长等待，防止某节因平台异常永久卡住拖死整个流程
LESSON_TIMEOUT_SECONDS = 3 * 3600


class StopRequested(Exception):
    """用户主动请求停止。从任意深度抛出，由 run() 统一收敛。"""


def _noop_stop() -> bool:
    return False


async def ensure_login(
    page: Page, context: BrowserContext, adapter: PlatformAdapter, config: Config,
    should_stop=_noop_stop,
) -> None:
    if await adapter.is_logged_in(page):
        logger.info("登录状态有效。")
        return
    logger.info("需要登录，正在处理...")
    login_task = asyncio.create_task(
        adapter.login(page, context, config.username, config.password)
    )
    try:
        while True:
            done, _ = await asyncio.wait({login_task}, timeout=0.5)
            if should_stop():
                raise StopRequested
            if done:
                await login_task
                break
    finally:
        if not login_task.done():
            login_task.cancel()
        await asyncio.gather(login_task, return_exceptions=True)
    logger.info("登录完成。", shift=True)
    await persist_login(context)


async def study_lesson(
    page: Page, adapter: PlatformAdapter, lesson: Lesson, clock: StudyClock,
    config: Config, should_stop=_noop_stop
) -> str:
    """播完一节。也会识别用户手动切课和独立章节测验。"""
    if not await adapter.enter_lesson(page, lesson):
        return "skipped"
    if lesson.kind == "chapter":
        return "chapter"
    if lesson.kind in ("ppt", "pdf"):
        if should_stop():
            raise StopRequested
        if config.limit_max_minutes and clock.reached(config.limit_max_minutes):
            return "limit"
        finished = await adapter.read_document(page, lesson, should_stop)
        if should_stop():
            raise StopRequested
        return "finished" if finished else "skipped"
    if await adapter.detect_chapter_test(page):
        return "chapter"

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
            active = await adapter.active_lesson_key(page)
            if lesson.key and active and active != lesson.key:
                return "switched"
            if await adapter.detect_chapter_test(page):
                return "chapter"
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
    config: Config, provider: AnswerProvider | None, cache: AnswerCache,
    should_stop=_noop_stop,
) -> bool:
    """学习一门课程。返回是否已完成本轮任务。"""
    title = await adapter.open_course(page, url)
    logger.info(f"当前课程：《{title}》", shift=True)
    confirm_progress = bool(getattr(adapter, "confirm_catalog_progress", False))

    # open_course 用这两个特殊返回值表示"根本没进对页面"，
    # 此时再去找章节只会得到一句误导性的"读不到章节列表"
    if title in ("未登录", "打开失败"):
        logger.error("没能正常进入课程页，本课程跳过。")
        await dump_page_structure(page, (), "open_course")
        return False

    await adapter.prepare_page(page)

    lessons = await adapter.list_lessons(page)
    if confirm_progress:
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
    if not pending:
        logger.info("所有小节均已标记完成，无需重复播放。")
        return True

    active_key = await adapter.active_lesson_key(page)
    index = next((i for i, item in enumerate(lessons) if item.key == active_key), 0)
    if lessons[index].finished:
        index = next((i for i, item in enumerate(lessons) if not item.finished), index)

    attempts: dict[str, int] = {}
    while True:
        # 每一轮重读目录，既拿到最新完成状态，也避免平台重绘后的旧节点。
        lessons = await adapter.list_lessons(page)
        if index >= len(lessons):
            if not confirm_progress:
                break
            index = next((i for i, item in enumerate(lessons)
                          if item.kind in ("video", "ppt", "pdf") and not item.finished
                          and attempts.get(item.key, 0) < 3), len(lessons))
            if index >= len(lessons):
                break
        lesson = lessons[index]
        if lesson.finished:
            index += 1
            continue

        logger.info(f"[{index + 1}/{len(lessons)}] {lesson.title}", shift=True)
        reason = await study_lesson(page, adapter, lesson, clock, config, should_stop)
        print()  # 结束 progress 的原地刷新行
        if reason == "switched":
            lessons = await adapter.list_lessons(page)
            active_key = await adapter.active_lesson_key(page)
            moved_to = next((i for i, item in enumerate(lessons)
                             if item.key == active_key), None)
            if moved_to is not None:
                index = moved_to
                logger.info(
                    f"检测到你手动切换章节，已从《{lessons[index].title}》接着处理。",
                    shift=True,
                )
                continue
        if reason == "limit":
            logger.info(
                f"已达设定时长上限 {config.limit_max_minutes} 分钟，停止本课程。", shift=True
            )
            return False
        if reason == "finished" and confirm_progress:
            if not await adapter.confirm_lesson_completion(page, lesson):
                reason = "unconfirmed"
        if (confirm_progress and lesson.kind in ("video", "ppt", "pdf")
                and reason in ("unconfirmed", "skipped", "timeout")):
            attempts[lesson.key] = attempts.get(lesson.key, 0) + 1
            if attempts[lesson.key] < 3:
                logger.warn(f"《{lesson.title}》目录未显示完成，重新学习（第 {attempts[lesson.key] + 1} 次）。")
                continue
            logger.warn(f"《{lesson.title}》尝试 3 次后目录仍未完成，先继续后续课件。")
        if reason == "finished":
            logger.info(f"《{lesson.title}》完成。")
            if config.answer_enabled and await adapter.open_chapter_test(page):
                await solve_chapter_test_once(page, adapter, config, provider, cache)
        elif reason == "chapter":
            if config.answer_enabled:
                handler = getattr(adapter, "process_chapter_test", None)
                if handler is not None:
                    await handler(page, provider, cache, config.answer_cache,
                                  config.auto_submit, should_stop)
                    if getattr(adapter, "course_page_lost", False):
                        logger.warn("平时测试后返回课程目录失败，本地址标记未完成，不再误报全部完成。")
                        return False
                else:
                    await solve_chapter_test_once(page, adapter, config, provider, cache)
            else:
                logger.info("独立章节测验已跳过（AI 答题未启用）。")
        elif reason == "skipped":
            logger.info("文档课件未能完成，已跳过。" if lesson.kind in ("ppt", "pdf") else "非视频任务点，已跳过。")
        index += 1

    if confirm_progress:
        remaining = [item for item in await adapter.list_lessons(page) if not item.finished]
        if remaining:
            logger.warn(f"《{title}》本轮结束，目录仍有 {len(remaining)} 节未达 100%，请查看日志。", shift=True)
            for item in remaining:
                logger.warn(f"未完成：《{item.title}》")
            logger.info("已记录未完成课件，继续处理下一个任务地址。")
            return False
        else:
            logger.info(f"《{title}》全部小节已由平台目录确认完成。", shift=True)
    else:
        logger.info(f"《{title}》全部小节处理完毕。", shift=True)
    return True


async def run(config: Config, should_stop=_noop_stop) -> bool:
    urls = config.course_urls
    unresolved = [u for u in urls if resolve(u) is None]
    if unresolved:
        logger.error(f"以下 URL 没有匹配的平台适配器：{unresolved}")
        logger.info(f"当前支持：{', '.join(supported_platforms())}")
        urls = [u for u in urls if resolve(u) is not None]
    if not urls:
        return False

    cache = AnswerCache(enabled=config.answer_cache)
    if config.answer_cache:
        total, hits = cache.stats()
        logger.info(f"本地题库已收录 {total} 道题，累计命中 {hits} 次。")

    provider = build_provider(config)
    if provider:
        logger.info(f"AI 作答已启用：{provider.name} / {config.model}")
    elif config.answer_enabled:
        logger.info("未启用 AI：视频弹题可试错，独立测验和考试无参考答案时留空。")
    if config.retry_until_correct:
        logger.info("视频弹题模式：答错自动换答案重试，直到平台判定正确。")
    else:
        logger.info("视频弹题模式：只作答一次" + ("并提交。" if config.auto_submit else "，不提交。"))
    clock = StudyClock()
    tasks: list[asyncio.Task] = []
    studied_any = False
    completed = not unresolved

    async with async_playwright() as p:
        page, context = await launch(p, config)
        try:
            for url in urls:
                if should_stop():
                    raise StopRequested
                adapter = resolve(url)
                if adapter is None:
                    continue
                logger.info("=" * 46, shift=True)
                clock.reset()
                if not await adapter.is_logged_in(page):
                    logger.info(f"正在确认{adapter.name}登录状态。")
                    await ensure_login(page, context, adapter, config, should_stop)
                if is_work_url(url):
                    if not config.answer_enabled:
                        logger.warn("AI 答题未启用，智慧树测试/考试不自动处理。")
                        completed = False
                        continue
                    await page.goto(url, wait_until="domcontentloaded")
                    processed = await study_work(
                        page, provider, cache, config.answer_cache,
                        config.exam_auto_submit if "doexamination" in url.lower()
                        else config.auto_submit, should_stop,
                    )
                    if processed:
                        logger.info("测试/考试页面保留供你核对；点软件「停止」结束。")
                        while not should_stop():
                            await asyncio.sleep(0.5)
                        raise StopRequested
                    logger.warn("智慧树测试/考试未完成，保留页面供人工检查。")
                    completed = False
                    if config.keep_browser_open:
                        while not should_stop():
                            await asyncio.sleep(0.5)
                        raise StopRequested
                    continue
                if is_exam_url(url):
                    if not config.answer_enabled:
                        logger.warn("AI 答题未启用，独立考试不自动处理。")
                        completed = False
                        continue
                    try:
                        exam_processed = await study_exam(
                            page, url, provider, cache, config.answer_cache,
                            config.exam_auto_submit, should_stop,
                        )
                    except Exception as exc:
                        logger.error(f"独立考试处理未完成：{Logger.summarize(exc)}")
                        exam_processed = False
                    if exam_processed:
                        logger.info("考试页面保持打开，供你核对作答与交卷状态；点软件的「停止」结束。")
                        while not should_stop():
                            await asyncio.sleep(0.5)
                        raise StopRequested
                    logger.warn("独立考试未完成，保留当前页面供人工检查。")
                    completed = False
                    if config.keep_browser_open:
                        while not should_stop():
                            await asyncio.sleep(0.5)
                        raise StopRequested
                    continue
                tasks = [
                    asyncio.create_task(playback_worker(page, adapter), name="playback"),
                    asyncio.create_task(tuning_worker(page, adapter, config), name="tuning"),
                    asyncio.create_task(captcha_worker(page, adapter, config, clock), name="captcha"),
                    asyncio.create_task(
                        question_worker(page, adapter, config, clock, provider, cache), name="question"
                    ),
                ]
                tasks.append(asyncio.create_task(task_monitor(tasks), name="monitor"))
                try:
                    try:
                        course_processed = await study_course(
                            page, adapter, url, clock, config, provider, cache, should_stop
                        )
                    except StopRequested:
                        raise
                    except Exception as exc:
                        if not getattr(adapter, "confirm_catalog_progress", False):
                            raise
                        logger.error(f"当前课程目录核对失败：{Logger.summarize(exc)}")
                        course_processed = False
                    if course_processed:
                        studied_any = True
                    else:
                        completed = False
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks = []
                logger.info(
                    f"本课程有效学习 {clock.elapsed_minutes:.1f} 分钟"
                    f"（另有 {clock.paused_minutes:.1f} 分钟为答题/验证等待，未计入）。"
                )
                if not course_processed and getattr(adapter, "confirm_catalog_progress", False):
                    logger.warn("当前任务地址未能完整处理，已记录问题并继续下一个地址。", shift=True)

            logger.info("=" * 46, shift=True)
            if studied_any:
                logger.info("全部课程本轮处理结束；如有未完成课件，请查看上方提示。")
            if config.answer_cache:
                total, hits = cache.stats()
                logger.info(f"本地题库现有 {total} 道题，累计命中 {hits} 次。")

            # 一节都没学成就直接关浏览器，用户只会看到"窗口一闪就没了"，
            # 既看不到出错页面也没法手动接管。留着窗口，由用户点停止再关。
            if not completed and config.keep_browser_open and not config.headless:
                logger.warn("有任务未能完成，浏览器先不关闭。", shift=True)
                logger.warn("你可以在浏览器里手动看看是哪一步不对；"
                            "看完点界面上的「停止」按钮即可关闭。")
                while not should_stop():
                    await asyncio.sleep(1)
            return completed and studied_any and not should_stop()
        except StopRequested:
            logger.info("已按你的要求停止。", shift=True)
            return False
        except Exception as exc:
            logger.log_exception("任务未完成，浏览器保留供检查。", exc)
            if config.keep_browser_open and not config.headless:
                while not should_stop():
                    await asyncio.sleep(1)
            return False
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if provider:
                await provider.aclose()
            cache.close()
