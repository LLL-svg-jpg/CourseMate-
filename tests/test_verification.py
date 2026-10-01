"""本地图像与离线 DOM 验证；不能替代真实平台验证码回执。"""
from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate import verification
from coursemate.events import StudyClock, VerificationTimeout
from coursemate.workers import captcha_worker


def png(array):
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return stream.getvalue()


def images(alpha=True):
    background = np.random.default_rng(5).integers(65, 240, (160, 320, 3), dtype=np.uint8)
    piece = np.zeros((160, 50, 4 if alpha else 3), dtype=np.uint8)
    piece[45:95, 5:45, :3] = background[45:95, 142:182]
    if alpha:
        piece[45:95, 5:45, 3] = 255
    return png(background), png(piece)


def data(image):
    return "data:image/png;base64," + base64.b64encode(image).decode()


def ocr_result(text, confidence=0.99):
    charset = [""] + list(dict.fromkeys(text))
    probabilities = []
    for char in text:
        probabilities.append([0.99] + [0.01 / (len(charset) - 1)] * (len(charset) - 1))
        row = [0.0] * len(charset)
        row[charset.index(char)] = confidence
        row[0] = 1 - confidence
        probabilities.append(row)
    return {"text": text, "charset": charset, "probabilities": probabilities}


def test_images():
    for alpha in (True, False):
        assert verification.slider_offset(*images(alpha)) == (137.0, 320)
    for bad in (b"invalid", png(np.zeros((10, 10, 4), np.uint8))):
        try:
            verification.slider_offset(images()[0], bad)
        except ValueError:
            pass
        else:
            raise AssertionError("无效滑块不应产生坐标")
    # 没有纹理的平色拼图需要匹配外轮廓；两个相同缺口则不能猜其中一个。
    import cv2
    textured_bg = np.random.default_rng(7).integers(65, 80, (160, 320, 3), dtype=np.uint8)
    outlined = np.zeros((160, 50, 4), np.uint8)
    outlined[45:95, 5:45, :3] = textured_bg[45:95, 142:182]
    outlined[45:95, 5:45, 3] = 255
    cv2.rectangle(outlined, (5, 45), (44, 94), (255, 255, 255, 255), 2)
    assert verification.slider_offset(png(textured_bg), png(outlined)) == (137.0, 320)
    shape = np.zeros((160, 50), np.uint8)
    cv2.rectangle(shape, (5, 45), (36, 85), 255, -1)
    cv2.circle(shape, (36, 65), 8, 255, -1)
    flat_piece = np.zeros((160, 50, 3), np.uint8)
    flat_piece[shape > 0] = 235
    flat_bg = np.full((160, 320, 3), 180, np.uint8)
    flat_bg[:, 137:187][shape > 0] = 100
    matched, width = verification.slider_offset(png(flat_bg), png(flat_piece))
    assert abs(matched - 137) <= 1 and width == 320
    flat_bg[:, 60:110][shape > 0] = 100
    try:
        verification.slider_offset(png(flat_bg), png(flat_piece))
    except ValueError:
        pass
    else:
        raise AssertionError("两个相同缺口必须留给人工")
    for text, confidence in (("1234", 0.99), ("1123", 0.99), ("1234", 0.7),
                             ("ab12", 0.99), ("1", 0.99), ("123456789", 0.99)):
        result = ocr_result(text, confidence)
        with patch("coursemate.verification._ocr", SimpleNamespace(classification=lambda *a, **k: result)):
            try:
                digits = verification.image_digits(b"synthetic")
            except ValueError:
                assert text not in ("1234", "1123") or confidence < 0.8
            else:
                assert digits == text and confidence >= 0.8
    # 使用实际安装的 OCR 和概率接口，验证模型能读取最基本的数字图片。
    image = Image.new("RGB", (160, 60), "white")
    ImageDraw.Draw(image).text((12, 5), "1234", font=ImageFont.truetype("arial.ttf", 42), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    assert verification.image_digits(buffer.getvalue()) == "1234"


async def test_dom():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page(device_scale_factor=2)
        try:
            for alpha in (True, False):
                bg, piece = images(alpha)
                bg_node = (f'<img class="yidun_bg-img" style="width:240px" src="{data(bg)}">' if alpha else
                           f'<div id="cx_imgBg" style="width:240px;height:120px;background-size:100% 100%;background-image:url({data(bg)})"></div>')
                piece_style = 'position:absolute;left:11.25px;top:0;width:37.5px'
                piece_node = (f'<img class="yidun_jigsaw" style="{piece_style}" src="{data(piece)}">' if alpha else
                              f'<div class="cx_imgBtn"><img style="{piece_style}" src="{data(piece)}"></div>')
                slider_class = "yidun_slider" if alpha else "cx_rightBtn"
                ratio = 0.74 if alpha else 1.27
                movement = (f'(e.clientX-start<36 ? (e.clientX-start)/2 : e.clientX-start-18)*{ratio}'
                            if alpha else f'Math.max(0,e.clientX-start-8)*{ratio}')
                await page.set_content('<div style="position:relative;margin-left:32px">' + bg_node + piece_node +
                    f'<div class="{slider_class}" style="width:40px;height:40px;background:gray"></div>' +
                    '<script>(()=>{window.moves=[];window.releases=0;'
                    'let start=null;const piece=document.querySelector(".yidun_jigsaw,.cx_imgBtn img");'
                    'document.addEventListener("mousedown",e=>start=e.clientX);'
                    'document.addEventListener("mousemove",e=>{moves.push(e.clientX);'
                    f'if(start!==null)piece.style.left=(11.25+{movement})+"px";' +
                    '});document.onmouseup=()=>{releases++;start=null};})();</script></div>')
                await page.locator("img").first.evaluate("el => el.decode()")
                assert await verification.attempt_verification(page)
                moves, releases = await page.evaluate("[moves,releases]")
                expected = (137 * 0.75 - 11.25) / ratio + (18 if alpha else 8)
                assert abs(moves[-1] - moves[0] - expected) <= 2
                actual = await page.locator(".yidun_jigsaw,.cx_imgBtn img").evaluate(
                    'el => parseFloat(el.style.left)')
                assert abs(actual - 137 * 0.75) <= 1.5
                assert releases == 1

            await page.locator(".cx_imgBtn img").evaluate('el => el.style.left="11.25px"')
            started, released = asyncio.Event(), []
            class Mouse:
                holding = False
                async def move(self, *args):
                    if self.holding:
                        started.set()
                        await asyncio.Event().wait()
                async def down(self): self.holding = True
                async def up(self): released.append(True)
            interrupted_page = SimpleNamespace(frames=page.frames, request=page.request, mouse=Mouse())
            task = asyncio.create_task(verification.attempt_verification(interrupted_page))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert released == [True]

            original_bytes = verification._image_bytes
            async def changed_image(page, node, background=False):
                result = await original_bytes(page, node, background)
                if background:
                    await page.locator(".cx_imgBtn img").evaluate('el=>el.src=el.src+"#new"')
                return result
            with patch("coursemate.verification._image_bytes", changed_image):
                assert not await verification.attempt_verification(page)
            assert await page.evaluate("releases") == 1

            await page.set_content('<iframe srcdoc="&lt;form&gt;'
                '&lt;input id=verifyCode&gt;&lt;img id=captchaImage src=' + data(images()[0]) + '&gt;'
                '&lt;button type=button onclick=&quot;this.dataset.clicked=1&quot;&gt;验证&lt;/button&gt;'
                '&lt;/form&gt;"></iframe>')
            frame = page.frames[-1]
            await frame.locator("#verifyCode").wait_for()
            assert await verification.has_verification(page)
            with patch("coursemate.verification.image_digits", return_value="1234"):
                assert await verification.attempt_verification(page)
            assert await frame.locator("#verifyCode").input_value() == "1234"
            assert await frame.locator("button").get_attribute("data-clicked") == "1"
            # 验证输入框还在时，单次操作绝不等于成功。
            adapter = SimpleNamespace(detect_captcha=lambda _: asyncio.sleep(0, result=False))
            calls = []
            async def attempt(_):
                calls.append(True)
                return True
            with patch("coursemate.verification.attempt_verification", attempt):
                task = asyncio.create_task(verification.wait_verification(page, adapter))
                await asyncio.sleep(0.05)
                assert not task.done() and calls == [True]
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            for button_text, separate in (("提交", False), ("确认", False), ("验证", True)):
                await page.set_content('<form><input id="verifyCode">' +
                    ("</form>" if separate else "") +
                    f'<img id="captchaImage" src="{data(images()[0])}">' +
                    f'<button type="button">{button_text}</button>' + ("" if separate else "</form>"))
                with patch("coursemate.verification.image_digits") as ocr:
                    assert not await verification.attempt_verification(page)
                    assert not ocr.called
                assert await page.locator("#verifyCode").input_value() == ""

            for placeholder, digits, accepted in (("图片验证码", "1234", True),
                                                   ("短信验证码", "1234", False),
                                                   ("图片验证码", "12345", False)):
                await page.set_content('<form><input name="username"><input type="password">' +
                    f'<input placeholder="{placeholder}" maxlength="4" pattern="[0-9]{{4}}">' +
                    f'<img alt="验证码" src="{data(images()[0])}">' +
                    '<button type="button" onclick="this.dataset.clicked=1">登录</button></form>')
                with patch("coursemate.verification.image_digits", return_value=digits):
                    try:
                        assert await verification.attempt_verification(page) == accepted
                    except ValueError:
                        assert not accepted and len(digits) > 4
                assert (await page.locator("button").get_attribute("data-clicked") == "1") == accepted
        finally:
            await browser.close()


async def test_worker_timeout():
    held = []
    clock = StudyClock()
    adapter = SimpleNamespace(detect_captcha=lambda _: asyncio.sleep(0, result=True))
    config = SimpleNamespace(beep_on_captcha=False, login_timeout_seconds=0.05)
    async def hold(page, adapter, enabled): held.append(enabled)
    async def never(*args): await asyncio.Event().wait()
    with (patch("coursemate.workers.hold_playback_for_manual_check", hold),
          patch("coursemate.workers.wait_verification", never)):
        try:
            await asyncio.wait_for(captcha_worker(None, adapter, config, clock), timeout=4)
        except VerificationTimeout:
            pass
        else:
            raise AssertionError("页面验证必须在时限后交给队列跳过")
    assert held == [True]  # 超时不能抢先恢复视频；队列随后关闭该会话。

    # 验证刚消失又出现时，也应立即恢复等待时限，不能空等半分钟。
    held.clear()
    waits = []
    original_sleep = asyncio.sleep
    async def quick_poll(seconds, result=None):
        return await original_sleep(0.01 if seconds == 3 else seconds, result=result)
    async def reappearing(*args):
        waits.append(True)
        if len(waits) == 2:
            raise VerificationTimeout
    with (patch("coursemate.workers.hold_playback_for_manual_check", hold),
          patch("coursemate.workers.wait_verification", reappearing),
          patch("coursemate.workers.asyncio.sleep", quick_poll)):
        try:
            await asyncio.wait_for(captcha_worker(None, adapter, config, clock), timeout=1)
        except VerificationTimeout:
            pass
        else:
            raise AssertionError("再次出现的验证必须及时进入等待")
    assert held == [True, False, True] and len(waits) == 2


if __name__ == "__main__":
    test_images()
    asyncio.run(test_dom())
    asyncio.run(test_worker_timeout())
    print("本地图像识别、两种滑块 DOM、数字验证码限定提交、未知反馈和验证超时：通过")
