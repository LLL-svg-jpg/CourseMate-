"""四个常驻后台协程。

这是"陪伴"的实质：主循环只管推进章节，这四个协程各自盯住一件事，
让播放不中断、题目被处理、异常被看见。

并发模型借鉴 Autovisor：每个协程独立轮询、独立容错，
任何一个挂掉都不会拖垮其余部分，由 task_monitor 统一上报。
"""
from __future__ import annotations

import asyncio

from playwright.async_api import Page

from .answer.ai import match_option
from .answer.base import AnswerProvider, AnswerResult
from .answer.cache import AnswerCache
from .answer.strategy import build_attempts, describe_attempt
from .config import Config
from .events import StudyClock
from .logger import Logger
from .platforms.base import PlatformAdapter

logger = Logger()

# 单个弹题的总耗时上限。超过就强行关窗继续播——
# 卡在一道题上不动，比这道题没答对严重得多。
QUESTION_TOTAL_TIMEOUT = 300

# 浏览器被用户关掉时，各协程应当安静退出而不是刷屏报错
_CLOSED_SIGNALS = (
    "Target closed",
    "Target page, context or browser has been closed",
    "Browser closed",
    "Connection closed",
)
# 高频轮询中的常规未命中，降级为 DEBUG，避免污染控制台
_EXPECTED_SIGNALS = (
    "waiting for locator",
    "waiting for selector",
    "No node found for selector",
    "Execution context was destroyed",
    "frame was detached",
    "Timeout",
)


def is_closed(exc: BaseException) -> bool:
    text = str(exc)
    return any(sig in text for sig in _CLOSED_SIGNALS)


def is_expected(exc: BaseException) -> bool:
    text = str(exc)
    return any(sig in text for sig in _EXPECTED_SIGNALS)


def _handle(exc: BaseException, who: str) -> bool:
    """统一的协程异常处理。返回 True 表示应当退出协程。"""
    if is_closed(exc):
        logger.debug(f"浏览器已关闭，{who} 停止运行。")
        return True
    if is_expected(exc):
        logger.debug(f"{who} 轮询未命中：{Logger.summarize(exc)}")
    else:
        logger.log_exception(f"{who} 执行异常。", exc)
    return False


async def task_monitor(tasks: list[asyncio.Task]) -> None:
    """监控其余协程，任何一个异常退出都要让用户知道。"""
    reported: set[asyncio.Task] = set()
    while any(not t.done() for t in tasks):
        for task in tasks:
            if task.done() and task not in reported:
                reported.add(task)
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None:
                    name = getattr(task.get_coro(), "__name__", "后台任务")
                    logger.log_exception(f"后台任务 {name} 异常结束。", exc, shift=True)
        await asyncio.sleep(1)


async def playback_worker(page: Page, adapter: PlatformAdapter) -> None:
    """续播：视频被暂停就恢复。

    平台会因为弹窗、失焦、切集等原因暂停视频，没有这个协程，
    程序会安静地"挂"在那里，进度一动不动。
    """
    while True:
        try:
            await asyncio.sleep(2)
            frame = await adapter.video_frame(page)
            paused = await frame.evaluate(
                "(() => { const v = document.querySelector('video');"
                " return v ? v.paused : null; })()"
            )
            if paused is True:
                await frame.evaluate(
                    "(() => { const v = document.querySelector('video');"
                    " if (v) v.play().catch(() => {}); })()"
                )
                logger.debug("检测到视频暂停，已恢复播放。")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _handle(exc, "续播模块"):
                return


async def tuning_worker(page: Page, adapter: PlatformAdapter, config: Config) -> None:
    """锁定倍速与静音。

    平台脚本会在切集、缓冲后把 playbackRate 重置回 1.0，
    所以必须持续巡检而不是设置一次了事。
    """
    while True:
        try:
            await asyncio.sleep(3)
            frame = await adapter.video_frame(page)
            speed = config.speed  # 每次读取，支持运行中改配置
            mute = config.mute
            await frame.evaluate(
                """([speed, mute]) => {
                    const v = document.querySelector('video');
                    if (!v) return;
                    if (Math.abs(v.playbackRate - speed) > 0.01) v.playbackRate = speed;
                    if (mute && v.volume !== 0) { v.volume = 0; v.muted = true; }
                }""",
                [speed, mute],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _handle(exc, "播放调节模块"):
                return


async def captcha_worker(
    page: Page, adapter: PlatformAdapter, config: Config, clock: StudyClock
) -> None:
    """人机验证监视。

    只检测并交还人工，不做任何破解。检测到后把等待时间从有效学习时长里扣除，
    否则限时刷课会被验证等待时间白白吃掉。
    """
    while True:
        try:
            await asyncio.sleep(3)
            if not await adapter.detect_captcha(page):
                continue

            # 这条带 [需要你处理] 前缀，界面据此把窗口叫到最前。
            # 不破解验证码是刻意的，那么"让人及时知道该来处理了"就得做扎实：
            # 窗口收在托盘里、被别的程序挡着时，光响一声铃很容易错过，
            # 一错过就白等在那儿，限时刷课的时间也跟着耗掉
            logger.warn("[需要你处理] 检测到人机验证，请回到浏览器手动完成验证...",
                        shift=True)
            if config.beep_on_captcha:
                print("\a", end="", flush=True)

            waited = 0.0
            while not await adapter.captcha_cleared(page):
                await asyncio.sleep(2)
                waited += 2
            clock.add_paused(waited)
            logger.info(f"人机验证已完成，本次等待 {waited:.0f} 秒不计入学习时长。", shift=True)
            # 验证刚过一段时间内不会再触发，避免空转
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _handle(exc, "人机验证模块"):
                return


async def solve_one_question(
    page: Page,
    adapter: PlatformAdapter,
    config: Config,
    question,
    provider: AnswerProvider | None,
    cache: AnswerCache,
) -> bool:
    """作答一道题，必要时反复尝试直到平台判定正确。返回是否答对。

    单答一次是不够的：答错的题平台会重新弹出，只答一次就关窗，
    同一道题会无限重弹，表现出来就是"视频卡住不动"。
    """
    logger.info(f"  题目：{question.describe()}")

    if not question.is_choice:
        logger.info("  非选择题，本程序不自动填写，需要你手动处理。")
        return False

    # 先拿一个起点答案：缓存优先，其次问 AI
    result = cache.get(question) if config.answer_cache else None
    if result is None and provider is not None:
        result = await provider.solve(question)
    if result is None:
        result = AnswerResult()

    ai_keys = match_option(result, question) if not result.empty else []
    if ai_keys:
        logger.info(f"  参考答案：{'+'.join(ai_keys)}（{result.source}，"
                    f"置信度 {result.confidence:.0%}）")
    else:
        logger.info("  无参考答案，将按选项顺序逐个尝试。")

    if not config.retry_until_correct:
        # 旧行为：只填一次，由用户决定是否提交
        if not ai_keys:
            logger.warn("  没有可填的答案，跳过本题。")
            return False
        await adapter.clear_selection(page, question)
        if await adapter.fill_answer(page, question, ai_keys, result):
            await adapter.submit_answer(page, config.auto_submit)
            return True
        logger.warn("  填写答案失败。")
        return False

    attempts = build_attempts(question, ai_keys)
    if not attempts:
        logger.warn("  无法生成候选答案，跳过本题。")
        return False

    for index, attempt in enumerate(attempts, 1):
        if not await adapter.detect_question(page):
            # 弹窗自己消失了，说明上一次尝试其实被判定通过
            logger.info("  题目已关闭，判定为通过。")
            return True

        logger.info(f"  {describe_attempt(attempt, index, len(attempts))}")
        await adapter.clear_selection(page, question)
        if not await adapter.fill_answer(page, question, attempt, result):
            logger.debug("  本次点选未生效，换下一组。")
            continue

        # 试错必须真提交，否则拿不到平台的对错反馈
        await adapter.submit_answer(page, auto_submit=True)
        feedback = await adapter.read_feedback(page)

        if feedback == "correct":
            logger.info(f"  ✓ 答对了（第 {index} 次尝试）。")
            # 验证过的答案比 AI 猜的可信得多，覆盖写回缓存
            if config.answer_cache:
                verified = AnswerResult(
                    option_keys=attempt,
                    option_texts=[o.text for o in question.options
                                  if o.key.upper() in attempt],
                    confidence=1.0,
                    reasoning="平台判定正确",
                    source="verified",
                )
                cache.put(question, verified)
            await adapter.confirm_and_close(page)
            return True

        if feedback == "unknown":
            logger.warn("  无法判断对错，停止尝试以免乱点。请留意这道题。")
            return False

        logger.debug(f"  第 {index} 次尝试判定为错误。")
        await asyncio.sleep(0.4)

    logger.warn(f"  已尝试 {len(attempts)} 次仍未答对，放弃本题以免卡住播放。")
    return False


async def question_worker(
    page: Page,
    adapter: PlatformAdapter,
    config: Config,
    clock: StudyClock,
    provider: AnswerProvider | None,
    cache: AnswerCache,
) -> None:
    """答题闭环：检测弹题 → 试错作答 → 确认关闭 → 让播放继续。

    这是本项目相对同类脚本的核心增量。Autovisor 在此处盲选前两个选项，
    OCS 依赖第三方题库；这里先查缓存、再问 AI，答错还会继续试到对。

    整段流程有总超时兜底：无论发生什么，最后一定会尝试关掉弹窗。
    宁可这道题没答对，也不能让视频永远停在那里。
    """
    while True:
        try:
            await asyncio.sleep(2)
            if not await adapter.detect_question(page):
                continue

            import time

            paused_at = time.time()
            try:
                await asyncio.wait_for(
                    _handle_question_popup(page, adapter, config, provider, cache),
                    timeout=QUESTION_TOTAL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warn(
                    f"答题超过 {QUESTION_TOTAL_TIMEOUT // 60} 分钟仍未结束，"
                    "强制关闭弹窗继续播放。", shift=True)
            finally:
                # 无论成功失败都要确保弹窗被关掉
                try:
                    if await adapter.detect_question(page):
                        await adapter.close_question(page)
                except Exception:
                    pass

            elapsed = time.time() - paused_at
            clock.add_paused(elapsed)
            logger.info(f"答题结束，耗时 {elapsed:.0f} 秒不计入学习时长，继续播放。", shift=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _handle(exc, "答题模块"):
                return
            try:
                await adapter.close_question(page)
            except Exception:
                pass


async def _handle_question_popup(
    page: Page,
    adapter: PlatformAdapter,
    config: Config,
    provider: AnswerProvider | None,
    cache: AnswerCache,
) -> None:
    questions = await adapter.extract_questions(page)
    if not questions:
        # 检测到弹窗但抠不出题目，至少要把它关掉，否则播放永久卡住
        logger.warn("检测到弹题但未能提取题干，直接关闭弹窗。")
        await adapter.close_question(page)
        return

    logger.info(f"检测到 {len(questions)} 道题目，暂停计时开始作答。", shift=True)
    solved = 0
    for question in questions:
        if await solve_one_question(page, adapter, config, question, provider, cache):
            solved += 1
    logger.info(f"本轮答对 {solved}/{len(questions)} 题。")
