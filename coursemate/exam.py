"""学习通独立考试页（与课程章节测验分开处理）。"""
from __future__ import annotations

import asyncio
import base64
import re
from urllib.parse import urlsplit

from playwright.async_api import Page

from .answer.ai import match_option
from .answer.base import AnswerProvider, AnswerResult, Option, Question
from .answer.cache import AnswerCache
from .logger import Logger

logger = Logger()
QUESTION_SEL = ".questionLi:visible"
OPTION_SEL = ".answerBg .answer_p"
NEXT_SEL = '[onclick*="getTheNextQuestion(1)"]'


def is_exam_url(url: str) -> bool:
    """只认学习通独立考试路径，避免把普通课程误当考试。"""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (host == "chaoxing.com" or host.endswith(".chaoxing.com")) and "/exam/test/" in parsed.path.lower()


async def _option_input(row, option, index: int, option_count: int):
    """新版考试可用自绘选项，旧版则有原生 radio/checkbox。"""
    selector = 'input[type="radio"], input[type="checkbox"]'
    for scope in (option, option.locator("xpath=..")):
        inputs = scope.locator(selector)
        if await inputs.count() == 1:
            return inputs.first
    for scope_selector in (".Cy_ulBottom", ".answerBg"):
        inputs = row.locator(scope_selector).locator(selector)
        if await inputs.count() == option_count:
            return inputs.nth(index)
    return None


async def _selected(option, native_input=None) -> bool:
    if native_input is not None:
        return await native_input.is_checked()
    return bool(await option.evaluate("""el => {
        const parent = el.parentElement;
        const box = parent && parent.querySelectorAll('.answer_p').length === 1 ? parent : el;
        return !!box.querySelector('input:checked, [aria-checked="true"], [class*="check_answer"]')
            || box.matches('[aria-checked="true"], .selected, .checked, [class*="check_answer"]');
    }"""))


async def extract_exam_questions(page: Page, capture_image: bool = True) -> list[Question]:
    questions: list[Question] = []
    rows = page.locator(QUESTION_SEL)
    for index in range(await rows.count()):
        row = rows.nth(index)
        title = row.locator(
            ".splitS-left .mark_name, .mark_name, :scope > h3, :scope > p, "
            ":scope > div:not(.answerBg,.stem_answer,.mark_answer):not(:has(.answer_p))"
        ).first
        if not await title.count():
            continue
        stem = " ".join((await title.inner_text()).split())
        type_field = row.locator('input[name^="type"]').first
        raw_type = await type_field.get_attribute("value") if await type_field.count() else ""
        qtype = {"0": "single", "1": "multiple", "3": "judge"}.get(raw_type or "", "unknown")
        options: list[Option] = []
        selected: list[str] = []
        option_rows = row.locator(OPTION_SEL)
        option_count = await option_rows.count()
        for option_index in range(option_count):
            option = option_rows.nth(option_index)
            raw_text = " ".join((await option.inner_text()).split())
            match = re.match(r"^([A-Z])[.、．\s]+(.+)$", raw_text, re.I)
            key = (match.group(1).upper() if match else chr(ord("A") + option_index))
            options.append(Option(key, match.group(2) if match else raw_text))
            if await _selected(option, await _option_input(row, option, option_index, option_count)):
                selected.append(key)
        image_data_url = ""
        if capture_image:
            try:
                png = await row.screenshot(type="png")
                image_data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            except Exception:
                pass
        questions.append(Question(
            stem=stem or f"考试第 {index + 1} 题", options=options, qtype=qtype,
            context="exam", index=index, selected_keys=selected,
            image_data_url=image_data_url,
        ))
    return questions


async def fill_exam_answer(page: Page, question: Question, keys: list[str], number: int = 0) -> bool:
    row = page.locator(QUESTION_SEL).nth(question.index)
    options = row.locator(OPTION_SEL)
    option_count = await options.count()
    inputs = [await _option_input(row, options.nth(i), i, option_count)
              for i in range(option_count)]
    if any([await _selected(options.nth(i), inputs[i]) for i in range(option_count)]):
        return False  # 用户或平台已保存的答案不得覆盖
    wanted = {key.upper() for key in keys}
    if not wanted or not wanted <= {opt.key for opt in question.options}:
        return False
    if question.qtype != "multiple" and len(wanted) != 1:
        return False
    for index, opt in enumerate(question.options):
        if opt.key in wanted:
            await options.nth(index).click(timeout=3000)
            native = inputs[index]
            if native is not None and not await native.is_checked():
                await native.evaluate("el => el.click()")
    await asyncio.sleep(0.3)  # 等待页面自己的选择/保存事件处理完成
    selected = {
        opt.key for index, opt in enumerate(question.options)
        if await _selected(options.nth(index), inputs[index])
    }
    if selected != wanted:
        logger.warn(f"考试本轮第 {number or question.index + 1} 题选中状态无法确认，保留页面供人工检查。")
        return False
    save = row.locator('[onclick*="saveQuestion"]').first
    if await save.count() and await save.is_visible():
        await save.click(timeout=3000)
    hidden = row.locator('input[type="hidden"][name^="answer"]:not([name^="answertype"])')
    if await hidden.count() and not any([
        (await hidden.nth(i).input_value()).strip()
        for i in range(await hidden.count())
    ]):
        await asyncio.sleep(0.5)
        if not any([
            (await hidden.nth(i).input_value()).strip()
            for i in range(await hidden.count())
        ]):
            logger.warn(f"考试本轮第 {number or question.index + 1} 题页面选项已亮起，但答案字段仍为空，不能确认写入。")
            return False
    return True


async def _answer_visible(page: Page, questions: list[Question], provider: AnswerProvider | None,
                          cache: AnswerCache, use_cache: bool, number_base: int) -> tuple[int, int]:
    answered = 0
    for question in questions:
        number = number_base + question.index + 1
        if question.selected_keys:
            answered += 1
            logger.info(f"考试本轮第 {number} 题已有答案，保留不覆盖。")
            continue
        if not question.is_choice:
            logger.warn(f"{question.describe()} 暂不支持自动填写，请人工处理。")
            continue
        result = cache.get(question) if use_cache else None
        if result is None and provider:
            result = await provider.solve(question)
        if not isinstance(result, AnswerResult) or result.empty:
            logger.warn(f"{question.describe()} 未找到答案，留空。")
            continue
        keys = match_option(result, question)
        if question.qtype == "multiple" and len(keys) < 2:
            logger.warn(f"考试本轮第 {number} 题是多选题，但 AI 只给出 {len(keys)} 个选项，留空供人工检查。")
            continue
        if keys and await fill_exam_answer(page, question, keys, number):
            answered += 1
            logger.info(f"考试本轮第 {number} 题（{question.qtype}）页面已选中：{'+'.join(keys)}")
    return answered, len(questions)


async def study_exam(page: Page, url: str, provider: AnswerProvider | None,
                     cache: AnswerCache, use_cache: bool, auto_submit: bool,
                     should_stop) -> bool:
    """只在可识别的考试页作答；不点击开考或验证码。"""
    await page.goto(url, wait_until="domcontentloaded")
    try:
        await page.locator(QUESTION_SEL).first.wait_for(state="visible", timeout=12000)
    except Exception:
        logger.warn("考试题目未出现；可能尚未开考、需要验证或页面结构不同，请人工检查。")
        return False
    logger.info("已识别学习通独立考试。AI 仅作答一次，不执行换答案试错。", shift=True)
    total_seen = 0
    all_answered = True
    seen_pages: set[tuple[str, ...]] = set()
    paged = False
    for _ in range(300):
        if should_stop():
            return False
        before = await extract_exam_questions(page, capture_image=bool(provider))
        signature = tuple(q.fingerprint for q in before)
        if not signature:
            all_answered = False
            logger.warn("考试题目结构无法识别，已停止翻页，保留页面供人工检查。")
            break
        if signature in seen_pages:
            logger.warn("考试页面没有出现新题，已停止翻页以免循环。")
            break
        seen_pages.add(signature)
        answered, count = await _answer_visible(page, before, provider, cache, use_cache, total_seen)
        total_seen += count
        all_answered &= answered == count
        next_button = page.locator(NEXT_SEL).first
        if (count != 1 or not await next_button.count()
                or not await next_button.is_visible() or not await next_button.is_enabled()):
            if count == 1 and answered:
                await asyncio.sleep(0.8)
                final_state = await extract_exam_questions(page, capture_image=False)
                if not final_state or not final_state[0].selected_keys:
                    logger.warn(f"考试本轮第 {total_seen} 题页面选项未保持选中，请人工补答。")
                    all_answered = False
                else:
                    hidden = page.locator(QUESTION_SEL).first.locator(
                        'input[type="hidden"][name^="answer"]:not([name^="answertype"])'
                    )
                    if await hidden.count() and not any([
                        (await hidden.nth(i).input_value()).strip()
                        for i in range(await hidden.count())
                    ]):
                        logger.warn(f"考试本轮第 {total_seen} 题选项可见，但答案字段仍为空，请人工确认保存。")
                        all_answered = False
            break
        paged = True
        await next_button.click(timeout=3000)
        for _ in range(20):
            if should_stop():
                return False
            await asyncio.sleep(0.25)
            current = await extract_exam_questions(page, capture_image=False)
            if current and tuple(q.fingerprint for q in current) != signature:
                break
        else:
            logger.warn("点击下一题后题目未变化，已停止，防止重复作答。")
            all_answered = False
            break
    logger.info(f"考试本轮已查看 {total_seen} 题；请在浏览器核对后手动交卷。", shift=True)
    if auto_submit:
        page_text = await page.locator("body").inner_text()
        declared = re.search(r"(?:共|总计)\s*(\d+)\s*题", page_text)
        declared_total = int(declared.group(1)) if declared else 0
        if paged or not all_answered or total_seen == 0 or declared_total != total_seen:
            logger.warn("未能确认整卷所有题目均已作答，自动交卷已取消。")
        else:
            button = page.locator('[onclick*="btnBlueSubmit"]').first
            if not await button.count():
                button = page.get_by_text(
                    re.compile(r"^(提交交卷|提交试卷|去交卷|交卷)$")
                ).first
            if await button.count() and await button.is_visible():
                await button.click(timeout=3000)
                logger.warn("已点击交卷按钮；如平台还有确认弹窗，需人工确认。")
            else:
                logger.warn("未找到明确的交卷按钮，未自动提交。")
    return True
