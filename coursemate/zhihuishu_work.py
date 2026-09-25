"""智慧树共享课的平时测试/考试页（stuExamWeb）。

只操作已经显示的选择题；选择后通过页面的“下一题”保存，交卷默认留给人工。
"""
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
ROW_SEL = ".examPaper_subject:visible"
OPTION_SEL = ".subject_node .nodeLab"
NEXT_SEL = "div.examPaper_box > div.switch-btn-box > button:nth-child(2)"


def is_work_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    fragment = parsed.fragment.lower()
    return (host == "zhihuishu.com" or host.endswith(".zhihuishu.com")) and (
        "stuexamweb.html" in parsed.path.lower()
        and ("dohomework" in fragment or "doexamination" in fragment)
    )


async def extract_questions(page: Page, capture_image: bool = True) -> list[Question]:
    questions: list[Question] = []
    rows = page.locator(ROW_SEL)
    for index in range(await rows.count()):
        row = rows.nth(index)
        title = row.locator(".subject_describe > div, .smallStem_describe > div:nth-child(2)").first
        if not await title.count():
            continue
        stem = " ".join((await title.inner_text()).split())
        if not stem:
            continue
        type_text = " ".join((await row.locator(".subject_type").first.inner_text()).split()) \
            if await row.locator(".subject_type").count() else ""
        qtype = ("multiple" if "多选" in type_text else
                 "judge" if "判断" in type_text else
                 "single" if "单选" in type_text else "unknown")
        options: list[Option] = []
        selected: list[str] = []
        labels = row.locator(OPTION_SEL)
        for option_index in range(await labels.count()):
            label = labels.nth(option_index)
            raw = " ".join((await label.inner_text()).split())
            match = re.match(r"^([A-Z])[.、．\s]+(.+)$", raw, re.I)
            key = match.group(1).upper() if match else chr(ord("A") + option_index)
            options.append(Option(key, match.group(2) if match else raw))
            checked = label.locator('input[type="radio"], input[type="checkbox"]').first
            if (await checked.count() and await checked.is_checked()) or \
                    "is-checked" in ((await label.get_attribute("class")) or "").split():
                selected.append(key)
        image_data_url = ""
        if capture_image:
            try:
                image_data_url = "data:image/png;base64," + base64.b64encode(
                    await row.screenshot(type="png")
                ).decode("ascii")
            except Exception:
                pass
        questions.append(Question(stem=stem, options=options, qtype=qtype,
                                  context="exam", index=index,
                                  selected_keys=selected, image_data_url=image_data_url))
    return questions


async def fill_answer(page: Page, question: Question, keys: list[str]) -> bool:
    wanted = {key.upper() for key in keys}
    if not wanted or not wanted <= {option.key for option in question.options}:
        return False
    if question.qtype != "multiple" and len(wanted) != 1:
        return False
    row = page.locator(ROW_SEL).nth(question.index)
    labels = row.locator(OPTION_SEL)
    # 页面既有答案不能覆盖，也不能靠最后的高亮假装写入成功。
    for index in range(await labels.count()):
        checked = labels.nth(index).locator('input[type="radio"], input[type="checkbox"]').first
        if await checked.count() and await checked.is_checked():
            return False
    for index, option in enumerate(question.options):
        if option.key in wanted:
            await labels.nth(index).click(timeout=3000)
    await asyncio.sleep(0.3)
    selected: set[str] = set()
    for index, option in enumerate(question.options):
        checked = labels.nth(index).locator('input[type="radio"], input[type="checkbox"]').first
        if await checked.count() and await checked.is_checked():
            selected.add(option.key)
    return selected == wanted


async def _save_answer_cards(page: Page, count: int) -> bool:
    """共享课逐题页通过逐项打开答题卡并点“下一题”写入答案。"""
    cards = page.locator(".answerCard_list ul li")
    if await cards.count() != count:
        return False
    for index in range(count):
        await cards.nth(index).click(timeout=3000)
        await asyncio.sleep(0.2)
        next_button = page.locator(NEXT_SEL).first
        if not await next_button.count() or not await next_button.is_enabled():
            return False
        await next_button.click(timeout=3000)
        await asyncio.sleep(0.2)
    return True


async def _submit_work(page: Page, label: str) -> None:
    before_url = page.url
    button = page.get_by_role("button", name=re.compile(r"^(提交|交卷|提交试卷)$")).first
    if not await button.count() or not await button.is_visible() or not await button.is_enabled():
        logger.warn("未找到明确提交按钮，保留页面供人工提交。")
        return
    await button.click(timeout=3000)
    await asyncio.sleep(0.4)
    dialog = page.locator(".el-message-box__wrapper:visible, .el-dialog__wrapper:visible") \
        .filter(has_text=re.compile(r"提交|交卷")).last
    if await dialog.count():
        confirm = dialog.get_by_role("button", name=re.compile(r"^(确定|确认|确认提交|提交)$")).first
        if await confirm.count() and await confirm.is_visible():
            await confirm.click(timeout=3000)
    await asyncio.sleep(0.8)
    text = (await page.locator("body").inner_text())[:1000]
    if page.url != before_url or any(word in text for word in ("提交成功", "已交卷", "已提交")):
        logger.info(f"智慧树{label}页面显示已提交；请在成绩/结果页最终核对。", shift=True)
    else:
        logger.warn(f"已点击智慧树{label}提交，但未看到成功回执；页面留供人工核对。")


async def study_work(page: Page, provider: AnswerProvider | None, cache: AnswerCache,
                     use_cache: bool, auto_submit: bool, should_stop) -> bool:
    """从已经打开的作业/考试题页作答，不代点“开始考试”。"""
    try:
        await page.locator(ROW_SEL).first.wait_for(state="visible", timeout=12000)
    except Exception:
        logger.warn("智慧树测试/考试题目未出现；可能还在列表、需要验证或页面结构不同。")
        return False
    is_exam = "doexamination" in urlsplit(page.url).fragment.lower()
    label = "考试" if is_exam else "平时测试"
    logger.info(f"已识别智慧树{label}；只填写可识别的选择题。", shift=True)
    seen: set[tuple[str, ...]] = set()
    total = answered = 0
    paged = False
    final_saved = False
    for _ in range(300):
        if should_stop():
            return False
        questions = await extract_questions(page, capture_image=bool(provider))
        signature = tuple(question.fingerprint for question in questions)
        if not signature or signature in seen:
            logger.warn("测试/考试没有出现新题，停止翻页并保留页面供人工检查。")
            break
        seen.add(signature)
        for question in questions:
            total += 1
            if question.selected_keys:
                answered += 1
                logger.info(f"{label}第 {total} 题已有答案，保留不覆盖。")
                continue
            if not question.is_choice:
                logger.warn(f"{label}第 {total} 题题型未识别，留给人工。")
                continue
            result = cache.get(question) if use_cache else None
            if result is None and provider:
                result = await provider.solve(question)
            if not isinstance(result, AnswerResult) or result.empty:
                logger.warn(f"{label}第 {total} 题没有参考答案，留空。")
                continue
            keys = match_option(result, question)
            if question.qtype == "multiple" and len(keys) < 2:
                logger.warn(f"{label}第 {total} 题为多选，答案不足两项，留空。")
                continue
            if keys and await fill_answer(page, question, keys):
                answered += 1
                logger.info(f"{label}第 {total} 题已选中：{'+'.join(keys)}")
            else:
                logger.warn(f"{label}第 {total} 题未能确认页面选中状态，请人工核对。")
        next_button = page.locator(NEXT_SEL).first
        if (len(questions) != 1 or not await next_button.count()
                or not await next_button.is_visible() or not await next_button.is_enabled()):
            save_button = page.get_by_role("button", name=re.compile(r"^(保存|暂存|保存答案)$")).first
            if await save_button.count() and await save_button.is_visible() and await save_button.is_enabled():
                await save_button.click(timeout=3000)
                final_saved = True
                logger.info("已点击页面的保存按钮；服务器是否落盘仍需在网页核对。")
            elif len(questions) == 1:
                logger.warn("最后一题没有明确保存按钮；页面选中不等于服务器已保存，请人工核对。")
            break
        paged = True
        await next_button.click(timeout=3000)  # 共享课逐题页靠“下一题”保存本题
        for _ in range(20):
            if should_stop():
                return False
            await asyncio.sleep(0.25)
            current = await extract_questions(page, capture_image=False)
            if current and tuple(q.fingerprint for q in current) != signature:
                break
        else:
            logger.warn("点击下一题后题目未变；最后一题是否保存需人工确认。")
            break
    logger.info(f"智慧树{label}本轮查看 {total} 题，确认选中 {answered} 题。", shift=True)
    if auto_submit:
        card_count = await page.locator(".answerCard_list ul li").count()
        if not is_exam and paged and not final_saved and answered == total:
            final_saved = await _save_answer_cards(page, total)
        if (total == 0 or answered != total or card_count != total
                or (paged and (is_exam or not final_saved))):
            logger.warn("无法确认整卷题数与已答题数一致，取消自动提交。")
        else:
            await _submit_work(page, label)
    else:
        logger.info("未自动交卷/提交，请在网页核对并人工操作。")
    return True
