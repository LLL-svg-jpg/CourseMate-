"""浏览器生命周期：启动、反检测注入、Cookie 持久化。"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright

from .config import Config, ConfigError, detect_browser
from .logger import Logger
from .stealth import STEALTH_JS, VISIBILITY_JS

logger = Logger()

from .paths import app_dir

COOKIE_PATH = app_dir() / "runtime" / "cookies.json"


def account_storage_path(account: dict[str, str]) -> Path:
    account_key = hashlib.sha256(account["id"].encode()).hexdigest()[:16]
    identity = json.dumps([account.get(key, "") for key in
                           ("platform", "username", "password")], ensure_ascii=False)
    identity_key = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return app_dir() / "runtime" / "accounts" / f"{account_key}-{identity_key}.json"


def load_cookies(path: Path = COOKIE_PATH) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) and data else None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warn(f"Cookie 文件损坏，将忽略：{Logger.summarize(exc)}")
        return None


def save_cookies(cookies: list[dict[str, Any]], path: Path = COOKIE_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warn(f"保存 Cookie 失败：{Logger.summarize(exc)}")


def clear_cookies(path: Path = COOKIE_PATH) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def launch(p: Playwright, config: Config,
                 account: dict[str, str] | None = None) -> tuple[Page, BrowserContext]:
    """启动浏览器并返回首个页面。

    Autovisor 的实战经验：Edge 首次启动有概率直接失败，重试一次即可恢复。
    这里对所有 channel 都做一次重试，成本低但能避免大量"启动失败"的困扰。
    """
    width, height = config.window_size
    channel = config.channel
    exe_path = config.executable_path
    if config.executable_path_raw:
        if not exe_path or not Path(exe_path).is_file():
            raise ConfigError(f"浏览器安装路径无效：{config.executable_path_raw}")
        name = Path(exe_path).name.lower()
        detected = {"chrome.exe": "chrome", "msedge.exe": "msedge",
                    "chromium.exe": "chromium"}.get(name)
        if detected is None:
            raise ConfigError("请选择 chrome.exe、msedge.exe 或 chromium.exe。")
        if config.channel_raw == "auto":
            channel = detected
        elif channel != detected:
            raise ConfigError("所选浏览器与安装路径不一致，请改选对应浏览器或选择 auto。")
    elif config.channel_raw == "auto":
        channel, exe_path = detect_browser()

    args = [
        # 关掉"Chrome 正受到自动测试软件的控制"提示条，它本身就是特征
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--mute-audio" if config.mute else "--no-default-browser-check",
    ]
    if config.maximize:
        # 注意：--start-maximized 与 --window-size 互斥，同时给会互相打架
        args.append("--start-maximized")
    else:
        args += [f"--window-size={width},{height}", "--window-position=80,60"]

    launch_args: dict[str, Any] = {
        "channel": channel,
        "headless": config.headless,
        "executable_path": exe_path,
        "args": args,
    }
    if channel == "chromium":
        launch_args.pop("channel", None)
    logger.info(f"正在启动 {channel} 浏览器...")
    try:
        browser = await p.chromium.launch(**launch_args)
    except Exception as exc:
        logger.warn(f"浏览器首次启动失败，正在重试：{Logger.summarize(exc)}")
        browser = await p.chromium.launch(**launch_args)

    return await open_context(browser, config, account)


async def open_context(browser, config: Config,
                       account: dict[str, str] | None = None) -> tuple[Page, BrowserContext]:
    width, height = config.window_size
    # no_viewport 是关键：给了固定 viewport，页面内容就被锁死在那个尺寸，
    # 用户把窗口拉大或最大化后，页面照旧按老尺寸渲染，看着就像没全屏。
    # 设成 True 后视口跟随窗口实际大小，拉多大页面就铺多大。
    context_args: dict[str, Any] = {
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "no_viewport": True,
    }
    if config.headless:
        # 无头模式下没有真实窗口，必须给个尺寸，否则默认 800x600 太小
        context_args.pop("no_viewport")
        context_args["viewport"] = {"width": width, "height": height}
    if account is not None:
        state_path = account_storage_path(account)
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) \
                        or not isinstance(state.get("origins"), list):
                    raise ValueError("登录状态格式错误")
                context_args["storage_state"] = state
            except (OSError, ValueError):
                logger.warn("该账号的登录状态损坏，需要重新登录。")
    context = await browser.new_context(**context_args)

    cookies = load_cookies() if account is None else None
    if cookies:
        await context.add_cookies(cookies)
        logger.info("已载入登录凭证，尝试免密登录。")
    else:
        logger.info("浏览器会话已隔离，将检查当前账号的登录状态。")

    # 必须在 new_page 之前注入，才能覆盖页面自身脚本执行前的时机
    await context.add_init_script(STEALTH_JS)
    await context.add_init_script(VISIBILITY_JS)

    page = await context.new_page()
    # 主循环用超长超时，具体任务里再临时收紧。
    # 这样"等不到元素"就等价于"页面处于异常态"，可以用超时作为状态信号。
    page.set_default_timeout(24 * 3600 * 1000)
    logger.debug("浏览器就绪，反检测脚本已注入。")
    return page, context


async def persist_login(context: BrowserContext,
                        account: dict[str, str] | None = None) -> None:
    if account is not None:
        state = await context.storage_state()
        path = account_storage_path(account)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.warn("保存当前账号的登录状态失败。")
        return
    cookies = await context.cookies()
    if cookies:
        save_cookies(cookies)
        logger.info(f"登录凭证已保存到 {COOKIE_PATH}，下次可免密登录。")
