"""阿里云拼图的控件检测和位移回归；真实通过另看服务端回执。"""
from __future__ import annotations

import asyncio
import base64
import io
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate.platforms.icve import IcveAdapter
from coursemate.aliyun_track import aliyun_track
from coursemate.verification import aliyun_offset, attempt_verification, has_verification


def data(array):
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def raw(array):
    return base64.b64decode(data(array).split(",", 1)[1])


def test_images():
    bg = np.random.default_rng(7).integers(65, 180, (160, 320, 3), dtype=np.uint8)
    mask = np.zeros((160, 50), np.uint8)
    mask[45:95, 5:40] = 255
    mask[60:80, 35:48] = 255
    piece = np.zeros((160, 50, 4), np.uint8)
    piece[:, :, :3] = bg[:, 137:187]
    piece[:, :, 3] = mask
    bg[:, 137:187][mask > 0] = 205  # 阿里云背景把缺口覆盖成平色。
    offset, width = aliyun_offset(raw(bg), raw(piece))
    assert abs(offset - 137) <= 2 and width == 320
    decorated = piece.copy()
    decorated[:, :, 3] = np.asarray(Image.fromarray(mask).filter(ImageFilter.MaxFilter(3)))
    offset, width = aliyun_offset(raw(bg), raw(decorated))
    assert abs(offset - 137) <= 2 and width == 320  # 外侧描边不改变缺口位置。
    bg[:, 60:110][mask > 0] = 205
    try:
        aliyun_offset(raw(bg), raw(piece))
    except ValueError:
        pass
    else:
        raise AssertionError("相同缺口并存时必须拒绝猜测")


def test_track():
    state = random.getstate()
    try:
        random.seed(17)
        for distance in range(40, 261):
            track = aliyun_track(distance)
            assert abs(sum(dx for dx, _, _ in track) - distance) < 1e-7
            assert all(math.isfinite(value) for row in track for value in row)
            assert all(dt > 0 for _, _, dt in track)
    finally:
        random.setstate(state)


async def run():
    bg = np.random.default_rng(5).integers(65, 240, (160, 320, 3), dtype=np.uint8)
    piece = np.zeros((160, 50, 4), dtype=np.uint8)
    piece[45:95, 5:45, :3] = bg[45:95, 142:182]
    piece[45:95, 5:45, 3] = 255
    bg[:, 137:187][piece[:, :, 3] > 0] = 205
    html = f'''<meta charset="utf-8">
      <div id="aliyunCaptcha-window-popup" style="position:relative;margin-left:32px">
        <span>拖动下方拼图完成验证</span>
        <img id="aliyunCaptcha-img" style="display:block;width:300px" src="{data(bg)}">
        <img id="aliyunCaptcha-puzzle" style="position:absolute;left:0;top:0;width:46.875px" src="{data(piece)}">
        <div id="aliyunCaptcha-sliding-slider" style="width:40px;height:40px;background:gray"></div>
      </div>
      <script>
        let start=null; window.releases=0;
        document.onmousedown=e=>{{if(e.target.id==='aliyunCaptcha-sliding-slider')start=e.clientX;}};
        document.onmousemove=e=>{{if(start!==null){{
          const distance=Math.max(0,Math.min(260,e.clientX-start));
          document.querySelector('#aliyunCaptcha-puzzle').style.left=
            Math.max(0,0.00355*distance**2+0.0769*distance-0.004)+'px';
        }}}};
        document.onmouseup=()=>{{if(start!==null)releases++; start=null;}};
      </script>'''
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        await page.route("https://sso.icve.com.cn/sso/auth", lambda route: route.fulfill(
            body=html, content_type="text/html"))
        await page.goto("https://sso.icve.com.cn/sso/auth")
        adapter = IcveAdapter()
        assert await adapter.detect_captcha(page)
        assert await has_verification(page)
        assert await attempt_verification(page)
        actual = await page.locator("#aliyunCaptcha-puzzle").evaluate("el=>parseFloat(el.style.left)")
        assert abs(actual - 137 * 300 / 320) <= 1.5
        assert await page.evaluate("releases") == 1
        assert await has_verification(page)  # 一次操作不等于验证成功。
        await page.locator("#aliyunCaptcha-window-popup").evaluate("el=>el.style.display='none'")
        assert not await has_verification(page)
        assert not await adapter.detect_captcha(page)
        assert not await attempt_verification(page)
        await page.reload()
        waiting = asyncio.create_task(attempt_verification(page))
        await asyncio.sleep(0.2)
        waiting.cancel()
        cancelled = await asyncio.gather(waiting, return_exceptions=True)
        assert isinstance(cancelled[0], asyncio.CancelledError)
        assert await page.evaluate('releases') == 0  # 取消采集等待时不会开始拖动。
        await page.reload()
        dragging = asyncio.create_task(attempt_verification(page))
        await page.wait_for_function('start!==null', timeout=10000)
        dragging.cancel()
        cancelled = await asyncio.gather(dragging, return_exceptions=True)
        assert isinstance(cancelled[0], asyncio.CancelledError)
        assert await page.evaluate('start===null && releases===1')  # 中止拖动也必须释放鼠标。
        await page.reload()
        await page.set_viewport_size({'width':1707,'height':950})
        await page.locator('#aliyunCaptcha-window-popup').evaluate("el=>el.style.marginLeft='200px'")
        assert await attempt_verification(page)
        actual = await page.locator("#aliyunCaptcha-puzzle").evaluate("el=>parseFloat(el.style.left)")
        assert abs(actual - 137 * 300 / 320) <= 1.5
        assert await page.evaluate("releases") == 1
        await page.reload()
        await page.locator('#aliyunCaptcha-img').evaluate("el=>el.style.width='240px'")
        try:
            await attempt_verification(page)
        except ValueError:
            pass
        else:
            raise AssertionError('未校准的控件尺寸必须拒绝猜测')
        assert await page.evaluate('releases') == 0
        await page.reload()
        duplicate = bg.copy()
        duplicate[:, 60:110][piece[:, :, 3] > 0] = 205
        await page.locator('#aliyunCaptcha-img').evaluate('(el,src)=>el.src=src', data(duplicate))
        await page.evaluate("""() => {
          const button=document.createElement('button'); button.id='aliyunCaptcha-btn-refresh';
          button.onclick=()=>document.body.dataset.refreshed='1'; document.body.append(button);
        }""")
        assert await attempt_verification(page)
        assert await page.locator('body').get_attribute('data-refreshed') == '1'
        assert await page.evaluate('releases') == 0  # 不确定图像只能刷新，不能猜坐标。
        await browser.close()


if __name__ == "__main__":
    test_images()
    test_track()
    asyncio.run(run())
    print("阿里云拼图：可见控件检测、实际位移、释放鼠标、隐藏控件不误报通过")
