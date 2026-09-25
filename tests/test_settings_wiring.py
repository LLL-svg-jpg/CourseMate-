"""设置项从配置到浏览器/收尾动作的离线回归测试。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate.browser import launch
from coursemate.config import Config, ConfigError
from coursemate.config_writer import save_config
from coursemate.gui import CourseMateGUI


class FakeContext:
    async def add_init_script(self, script):
        pass

    async def new_page(self):
        return SimpleNamespace(set_default_timeout=lambda value: None)


class FakeChromium:
    def __init__(self):
        self.launch_args = None
        self.context_args = None

    async def launch(self, **kwargs):
        self.launch_args = kwargs
        owner = self

        class Browser:
            async def new_context(self, **context_kwargs):
                owner.context_args = context_kwargs
                return FakeContext()

        return Browser()


def fake_config(channel, path="", *, maximize=True, headless=False):
    return SimpleNamespace(channel=channel, channel_raw=channel,
                           executable_path=path or None, executable_path_raw=path,
                           window_size=(1440, 900), maximize=maximize,
                           headless=headless, mute=True)


def test_browser_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        edge = Path(tmp) / "msedge.exe"
        edge.touch()
        engine = FakeChromium()
        with patch("coursemate.browser.load_cookies", return_value=None):
            asyncio.run(launch(SimpleNamespace(chromium=engine), fake_config("auto", str(edge))))
        assert engine.launch_args["channel"] == "msedge"
        assert engine.launch_args["executable_path"] == str(edge)
        assert "--start-maximized" in engine.launch_args["args"]
        assert engine.context_args["no_viewport"] is True

        engine = FakeChromium()
        with patch("coursemate.browser.load_cookies", return_value=None), \
             patch("coursemate.browser.detect_browser", side_effect=AssertionError("unexpected fallback")):
            asyncio.run(launch(SimpleNamespace(chromium=engine), fake_config("chromium", headless=True)))
        assert "channel" not in engine.launch_args
        assert engine.launch_args["headless"] is True
        assert engine.context_args["viewport"] == {"width": 1440, "height": 900}

        engine = FakeChromium()
        with patch("coursemate.browser.load_cookies", return_value=None), \
             patch("coursemate.browser.detect_browser", side_effect=AssertionError("unexpected fallback")):
            asyncio.run(launch(SimpleNamespace(chromium=engine), fake_config("msedge", maximize=False)))
        assert engine.launch_args["channel"] == "msedge"
        assert "--window-size=1440,900" in engine.launch_args["args"]

        try:
            asyncio.run(launch(SimpleNamespace(chromium=engine), fake_config("chrome", str(edge))))
        except ConfigError:
            pass
        else:
            raise AssertionError("不匹配的浏览器路径应明确报错")


def test_config_browser_path_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        edge = Path(tmp) / "msedge.exe"
        edge.touch()
        config_path = Path(tmp) / "config.toml"
        save_config(config_path, {"channel": "auto", "executable_path": tmp})
        config = Config(config_path)
        assert config.executable_path_raw == tmp
        assert config.executable_path == str(edge)
        edge.unlink()
        chromium = Path(tmp) / "chromium.exe"
        chromium.touch()
        assert config.executable_path == str(chromium)


def test_other_settings_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.toml"
        save_config(config_path, {
            "maximize": False, "keep_open_on_failure": False, "headless": True,
            "autorun": True, "start_minimized": True, "always_on_top": True,
            "beep_on_captcha": False, "captcha_popup": False,
            "on_finish": "quit", "proxy": "http://127.0.0.1:7890",
            "log_level": "DEBUG", "cache": False, "font_size": 18,
        })
        config = Config(config_path)
        assert not config.maximize and not config.keep_browser_open and config.headless
        assert config.autorun and config.start_minimized and config.always_on_top
        assert not config.beep_on_captcha and not config.captcha_popup
        assert config.on_finish == "quit" and config.proxy == "http://127.0.0.1:7890"
        assert config.log_level == "DEBUG" and not config.answer_cache
        assert config.font_size == 18


def test_finish_action_only_after_success() -> None:
    import threading

    scheduled = []
    app = object.__new__(CourseMateGUI)
    app.stop_event = threading.Event()
    app.root = SimpleNamespace(after=lambda delay, callback: scheduled.append((delay, callback)))
    app.logger = SimpleNamespace(save=lambda: None, log_exception=lambda *args: None)
    app._set_running = lambda value: None
    app._append_log = lambda *args: None
    app._run_finish_action = lambda: None

    async def failed(*args, **kwargs):
        return False

    async def succeeded(*args, **kwargs):
        return True

    with patch("coursemate.runner.run", failed):
        app._run_worker(object())
    assert not any(delay == 500 for delay, _ in scheduled)
    scheduled.clear()
    with patch("coursemate.runner.run", succeeded):
        app._run_worker(object())
    assert any(delay == 500 for delay, _ in scheduled)


def test_save_settings_without_course() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = object.__new__(CourseMateGUI)
        app.root = object()
        app._collect = lambda: {"urls": [], "channel": "edge", "maximize": False}
        app._append_log = lambda *args: None
        config_path = Path(tmp) / "config.toml"
        with patch("coursemate.gui.CONFIG_PATH", config_path):
            assert app.save()
        config = Config(config_path)
        assert config.course_urls == []
        assert config.channel_raw == "edge" and not config.maximize


def test_real_browser_maximize() -> None:
    from playwright.async_api import async_playwright
    from coursemate.config import find_installed_browser

    if not find_installed_browser("msedge"):
        return

    async def check() -> None:
        async with async_playwright() as playwright:
            with patch("coursemate.browser.load_cookies", return_value=None):
                page, context = await launch(playwright, fake_config("msedge"))
            try:
                cdp = await context.new_cdp_session(page)
                window = await cdp.send("Browser.getWindowForTarget")
                bounds = await cdp.send("Browser.getWindowBounds", {"windowId": window["windowId"]})
                assert bounds["bounds"]["windowState"] == "maximized", bounds
            finally:
                await context.browser.close()

    asyncio.run(check())


if __name__ == "__main__":
    test_browser_selection()
    test_config_browser_path_roundtrip()
    test_other_settings_roundtrip()
    test_finish_action_only_after_success()
    test_save_settings_without_course()
    test_real_browser_maximize()
    print("设置链路测试通过")
