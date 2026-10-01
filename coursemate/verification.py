"""识别已匹配的页面验证控件；是否通过始终由网页反馈决定。"""
from __future__ import annotations

import asyncio
import base64
import random
import re
import time
from urllib.parse import urlparse

from .logger import Logger

logger = Logger()


def slider_offset(background: bytes, piece: bytes) -> tuple[float, int]:
    import cv2
    import numpy as np

    bg = cv2.imdecode(np.frombuffer(background, np.uint8), cv2.IMREAD_COLOR)
    target = cv2.imdecode(np.frombuffer(piece, np.uint8), cv2.IMREAD_UNCHANGED)
    if bg is None or target is None or len(target.shape) != 3:
        raise ValueError("未识别图像格式")
    if target.shape[0] > bg.shape[0] or target.shape[1] >= bg.shape[1]:
        raise ValueError("滑块图像尺寸不匹配")
    if target.shape[2] == 4:
        mask = (target[:, :, 3] > 128).astype(np.uint8) * 255
        target = target[:, :, :3]
    else:
        mask = (cv2.cvtColor(target, cv2.COLOR_BGR2GRAY) > 40).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise ValueError("未识别滑块轮廓")
        mask[:] = 0
        cv2.drawContours(mask, [max(contours, key=cv2.contourArea)], -1, 255, -1)
    if not np.any(mask):
        raise ValueError("滑块图像为空")
    inner_mask = cv2.erode(mask, np.ones((7, 7), np.uint8))
    if not np.any(inner_mask):
        raise ValueError("滑块有效区域过小")
    # 控件给拼图加的白色或黄色描边不属于背景纹理。
    scores = cv2.matchTemplate(bg, target, cv2.TM_CCOEFF_NORMED, mask=inner_mask)
    scores[~np.isfinite(scores)] = -1
    _, confidence, _, position = cv2.minMaxLoc(scores)
    if confidence < 0.6:
        # 平色拼图没有足够纹理，改用轮廓；相似缺口并存时拒绝猜测。
        x, y, width, height = cv2.boundingRect(mask)
        outline = cv2.Canny(mask, 50, 150)[y:y + height, x:x + width]
        edges = cv2.Canny(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), 30, 80)[y:y + height]
        outline = cv2.GaussianBlur(outline, (3, 3), 0)
        edges = cv2.GaussianBlur(edges, (3, 3), 0)
        shape_scores = cv2.matchTemplate(edges, outline, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, shape_position = cv2.minMaxLoc(shape_scores)
        rivals = shape_scores.copy()
        exclusion = max(5, width // 3)
        rivals[:, max(0, shape_position[0] - exclusion):shape_position[0] + exclusion + 1] = -1
        if confidence < 0.55 or confidence - float(rivals.max()) < 0.12:
            raise ValueError("滑块轮廓匹配不可靠或存在多个候选")
        position = shape_position[0] - x, 0
    if not 0 < position[0] <= bg.shape[1] - target.shape[1]:
        raise ValueError("滑块识别结果不可靠")
    return float(position[0]), int(bg.shape[1])


_ocr = None


def aliyun_offset(background: bytes, piece: bytes) -> tuple[float, int]:
    """阿里云背景遮住原纹理，按透明拼图的外轮廓找唯一缺口。"""
    import cv2
    import numpy as np

    bg = cv2.imdecode(np.frombuffer(background, np.uint8), cv2.IMREAD_COLOR)
    target = cv2.imdecode(np.frombuffer(piece, np.uint8), cv2.IMREAD_UNCHANGED)
    if bg is None or target is None or len(target.shape) != 3 or target.shape[2] != 4:
        raise ValueError("未识别阿里云拼图格式")
    if target.shape[0] != bg.shape[0] or target.shape[1] >= bg.shape[1]:
        raise ValueError("阿里云拼图尺寸不匹配")
    mask = (target[:, :, 3] > 128).astype(np.uint8) * 255
    if not np.any(mask):
        raise ValueError("阿里云拼图为空")
    x, y, width, height = cv2.boundingRect(mask)
    # 拼图的外侧灰色描边比背景缺口多一像素，先去掉装饰边。
    contour = cv2.Canny(cv2.erode(mask, np.ones((3, 3), np.uint8)), 50, 150)
    outline = cv2.GaussianBlur(contour, (3, 3), 0)
    candidates = []
    for low, high in ((10, 30), (30, 80)):
        edges = cv2.GaussianBlur(cv2.Canny(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), low, high), (3, 3), 0)
        for padding in (0, 2):
            left, top = max(0, x - padding), max(0, y - padding)
            bottom = min(bg.shape[0], y + height + padding)
            template = outline[top:bottom, left:min(target.shape[1], x + width + padding)]
            search = edges[max(0, top - 4):min(bg.shape[0], bottom + 4)]
            scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
            _, confidence, _, position = cv2.minMaxLoc(scores)
            rivals = scores.copy()
            exclusion = max(5, width // 3)
            rivals[:, max(0, position[0] - exclusion):position[0] + exclusion + 1] = -1
            offset = position[0] - left
            if (confidence >= 0.3 and confidence - float(rivals.max()) >= 0.12
                    and 0 < offset <= bg.shape[1] - target.shape[1]):
                candidates.append((confidence, offset))
            # 平色或暗色背景的相关系数较低；要求轮廓距离小且第二候选明显更远。
            binary = (contour[top:bottom, left:min(target.shape[1], x + width + padding)] > 0).astype(np.float32)
            distances = cv2.distanceTransform(
                255 - cv2.Canny(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), low, high), cv2.DIST_L2, 3)
            search = distances[max(0, top - 4):min(bg.shape[0], bottom + 4)]
            costs = cv2.matchTemplate(search, binary, cv2.TM_CCORR) / binary.sum()
            cost, _, position, _ = cv2.minMaxLoc(costs)
            rivals = costs.copy()
            rivals[:, max(0, position[0] - exclusion):position[0] + exclusion + 1] = 999
            offset = position[0] - left
            if (cost <= 2 and float(rivals.min()) - cost >= 0.5
                    and 0 < offset <= bg.shape[1] - target.shape[1]):
                candidates.append((1 / (1 + cost), offset))
    if not candidates:
        raise ValueError("阿里云拼图轮廓不可靠或存在多个候选")
    return float(max(candidates)[1]), int(bg.shape[1])


def image_digits(image: bytes) -> str:
    import ddddocr
    import numpy as np
    from ddddocr.utils.exceptions import DDDDOCRError

    global _ocr
    if _ocr is None:
        _ocr = ddddocr.DdddOcr(show_ad=False)
    try:
        result = _ocr.classification(image, probability=True)
    except DDDDOCRError as exc:
        raise ValueError("数字验证码识别失败") from exc
    text = result.get("text", "")
    if not re.fullmatch(r"[0-9]{2,8}", text):
        raise ValueError("未确认纯数字验证码")
    probabilities = np.asarray(result["probabilities"])
    probabilities = probabilities.reshape(-1, len(result["charset"]))
    indices = probabilities.argmax(axis=1)
    characters, confidence = [], []
    previous = 0
    for index, score in zip(indices, probabilities.max(axis=1)):
        if index and index != previous:
            characters.append(result["charset"][index])
            confidence.append(float(score))
        elif index and confidence:
            confidence[-1] = max(confidence[-1], float(score))
        previous = index
    if "".join(characters) != text or not confidence or min(confidence) < 0.8:
        raise ValueError("数字验证码识别结果不可靠")
    return text


async def _image_source(node, background=False) -> str:
    if background:
        source = await node.evaluate("el => getComputedStyle(el).backgroundImage")
        match = re.fullmatch(r'''url\(["']?(.*?)["']?\)''', source or "")
        source = match.group(1) if match else ""
    else:
        source = await node.get_attribute("src") or ""
    return source


async def _image_bytes(page, node, background=False) -> bytes:
    source = await _image_source(node, background)
    if source.startswith("data:"):
        return base64.b64decode(source.split(",", 1)[1])
    if not source:
        raise ValueError("验证码图片尚未加载")
    response = await page.request.get(source, timeout=5000)
    if not response.ok:
        raise ValueError("验证码图片获取失败")
    return await response.body()


_DIGIT_FIELDS = 'input#verifyCode, input[name="verifyCode"], input[name="captcha"], input[placeholder*="验证码"]'
_DIGIT_IMAGES = '#captchaImage, img[alt*="验证码"], img[id*="captcha" i], img[id*="verify" i]'


async def _numeric_controls(frame):
    fields = frame.locator(_DIGIT_FIELDS)
    for index in range(await fields.count()):
        field = fields.nth(index)
        if not await field.is_visible():
            continue
        description = " ".join([await field.get_attribute(key) or ""
                                for key in ("name", "id", "placeholder", "autocomplete")])
        if re.search(r"短信|手机|sms|one-time-code", description, re.I):
            continue
        form = field.locator("xpath=ancestor::form[1]")
        if not await form.count():
            continue
        images = form.locator(_DIGIT_IMAGES)
        visible = [images.nth(i) for i in range(await images.count())
                   if await images.nth(i).is_visible()]
        if len(visible) == 1:
            return field, visible[0], form
    return None


async def _drag_aliyun(page, x, y, distance):
    from .aliyun_track import aliyun_track

    track = aliyun_track(distance)
    await page.mouse.move(x - random.uniform(60, 120), y - random.uniform(0, 20), steps=10)
    await asyncio.sleep(random.uniform(0.2, 0.4))
    await page.mouse.move(x, y, steps=5)
    await asyncio.sleep(random.uniform(0.25, 0.5))
    cdp = await page.context.new_cdp_session(page)

    async def event(kind, buttons):
        params = {"type": kind, "x": x, "y": y, "button": "left",
                  "buttons": buttons, "pointerType": "mouse"}
        if kind != "mouseMoved":
            params["clickCount"] = 1
        await cdp.send("Input.dispatchMouseEvent", params)

    try:
        await event("mousePressed", 1)
        await asyncio.sleep(random.uniform(0.05, 0.11))
        started = time.perf_counter()
        schedule = 0
        for dx, dy, dt in track:
            schedule += dt / 1000
            x += dx
            y += dy
            await event("mouseMoved", 1)
            delay = schedule - (time.perf_counter() - started)
            if delay > 0:
                await asyncio.sleep(delay)
        await asyncio.sleep(random.uniform(0.01, 0.04))
    finally:
        try:
            await event("mouseReleased", 0)
        finally:
            await cdp.detach()


async def attempt_verification(page) -> bool:
    """返回是否进行过一次操作，绝不把它当作验证成功。"""
    for frame in page.frames:
        for background_sel, piece_sel, slider_sel, css_background in (
            (".yidun_bg-img", ".yidun_jigsaw", ".yidun_slider", False),
            ("#cx_imgBg", ".cx_imgBtn img", ".cx_rightBtn", True),
            ("#aliyunCaptcha-img", "#aliyunCaptcha-puzzle", "#aliyunCaptcha-sliding-slider", False),
        ):
            if background_sel == "#aliyunCaptcha-img" and urlparse(frame.url).hostname != "sso.icve.com.cn":
                continue
            background = frame.locator(background_sel).first
            piece = frame.locator(piece_sel).first
            slider = frame.locator(slider_sel).first
            if not all([await node.count() and await node.is_visible()
                        for node in (background, piece, slider)]):
                continue
            sources = await _image_source(background, css_background), await _image_source(piece)
            bg_bytes, piece_bytes = await asyncio.gather(
                _image_bytes(page, background, css_background), _image_bytes(page, piece))
            if background_sel == "#aliyunCaptcha-img":
                # 给 SDK 留出环境采集时间；外层仍负责登录超时与停止取消。
                await asyncio.sleep(2)
            matcher = aliyun_offset if background_sel == "#aliyunCaptcha-img" else slider_offset
            try:
                offset, image_width = await asyncio.to_thread(matcher, bg_bytes, piece_bytes)
            except ValueError:
                if background_sel == "#aliyunCaptcha-img":
                    refresh = frame.locator("#aliyunCaptcha-btn-refresh").first
                    if await refresh.count() and await refresh.is_visible():
                        await refresh.click(timeout=5000)
                        return True
                raise
            if sources != (await _image_source(background, css_background), await _image_source(piece)):
                return False
            bg_box, piece_box, slider_box = (
                await background.bounding_box(), await piece.bounding_box(), await slider.bounding_box())
            if not bg_box or not piece_box or not slider_box:
                return False
            if bg_box["width"] <= piece_box["width"] or bg_box["width"] <= slider_box["width"]:
                raise ValueError("滑块控件尺寸不匹配")
            if background_sel == "#aliyunCaptcha-img" and abs(bg_box["width"] - 300) > 0.1:
                raise ValueError("未校准当前阿里云控件尺寸")
            target_x = bg_box["x"] + offset * bg_box["width"] / image_width
            remaining = target_x - piece_box["x"]
            if remaining <= 0:
                raise ValueError("拼图目标不在可移动范围内")
            x = slider_box["x"] + slider_box["width"] / 2
            y = slider_box["y"] + slider_box["height"] / 2
            if background_sel == "#aliyunCaptcha-img":
                # 当前 300px 控件的本机实测二次曲线，鼠标坐标仍使用 CSS 像素。
                limit = bg_box["width"] - slider_box["width"]
                a, b, c = 0.00355, 0.0769, -0.004
                distance = (-b + (b * b + 4 * a * (remaining - c)) ** 0.5) / (2 * a)
                if not 0 < distance <= limit:
                    raise ValueError("阿里云拼图目标超出可拖动范围")
                await _drag_aliyun(page, x, y, distance)
                return True
            await page.mouse.move(x, y)
            await page.mouse.down()
            try:
                # 读取网页 SDK 实际移动的拼图，避免假设手柄与拼图等宽、等速。
                probe = min(max(16, piece_box["width"] - slider_box["width"] + 8), remaining / 3)
                await page.mouse.move(x + probe, y)
                await asyncio.sleep(0.08)
                first_box = await piece.bounding_box()
                await page.mouse.move(x + probe * 2, y)
                await asyncio.sleep(0.08)
                moved_box = await piece.bounding_box()
                ratio = (moved_box["x"] - first_box["x"]) / probe if moved_box and first_box else 0
                if not 0.2 <= ratio <= 3:
                    raise ValueError("未确认拼图随拖动移动")
                position = probe * 2
                for _ in range(2):
                    current = await piece.bounding_box()
                    if not current:
                        raise ValueError("拖动时拼图已消失")
                    distance = position + (target_x - current["x"]) / ratio
                    start = position
                    for step in range(1, 25):
                        t = step / 24
                        position = start + (distance - start) * (3 * t * t - 2 * t * t * t)
                        await page.mouse.move(x + position, y)
                        await asyncio.sleep(0.025)
                    await asyncio.sleep(0.08)
                    final_box = await piece.bounding_box()
                    if final_box and abs(final_box["x"] - target_x) <= 1.5:
                        break
                    if final_box and abs(position - start) > 1:
                        measured = (final_box["x"] - current["x"]) / (position - start)
                        if 0.2 <= measured <= 3:
                            ratio = measured
                else:
                    raise ValueError("拼图尚未对齐目标")
            finally:
                await page.mouse.up()
            return True

        controls = await _numeric_controls(frame)
        if not controls:
            continue
        field, image, form = controls
        button = form.get_by_role("button", name=re.compile(r"^(验证|校验|提交验证码|确认验证码)$")).first
        if not await button.count() and await form.locator('input[type="password"]:visible').count():
            button = form.get_by_role("button", name="登录", exact=True).first
        if not await button.count() or not await button.is_visible():
            return False
        source = await _image_source(image)
        digits = await asyncio.to_thread(image_digits, await _image_bytes(page, image))
        if source != await _image_source(image) or not await field.is_visible():
            return False
        maxlength = await field.get_attribute("maxlength")
        valid = await field.evaluate("(el, text) => { const copy = el.cloneNode(); copy.value = text; return copy.checkValidity(); }", digits)
        if (maxlength and len(digits) > int(maxlength)) or not valid:
            raise ValueError("识别结果与验证码输入要求不符")
        await field.fill(digits)
        await button.click()
        return True
    return False


async def has_verification(page) -> bool:
    for frame in page.frames:
        selectors = (".yidun_modal__title", ".cx_comImageValidate")
        if urlparse(frame.url).hostname == "sso.icve.com.cn":
            selectors += ("#aliyunCaptcha-window-popup",)
        for selector in selectors:
            node = frame.locator(selector).first
            if await node.count() and await node.is_visible():
                return True
        if await _numeric_controls(frame):
            return True
    return False


async def wait_verification(page, adapter) -> None:
    """最多三次本地操作，之后继续等待人工；外层统一执行超时和取消。"""
    from playwright.async_api import Error as PlaywrightError

    attempts = 0
    warned = False
    while await adapter.detect_captcha(page) or await has_verification(page):
        if attempts < 3:
            try:
                if await attempt_verification(page):
                    attempts += 1
            except (ValueError, ImportError, PlaywrightError):
                attempts += 1
                # 不猜坐标、不填不确定的文字，留给人工或外层超时跳过。
                if not warned:
                    logger.warn("当前验证未能本地处理，请在等待时限内手动完成；超时将跳过当前地址。")
                    warned = True
            await asyncio.sleep(2)
        else:
            await asyncio.sleep(0.25)
