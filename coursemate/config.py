"""TOML 配置加载，带热重载。

热重载借鉴自 Autovisor：把易变项做成 property，每次读取时重新解析文件，
这样程序跑着的时候改倍速、改时长限制立刻生效，不用重启。
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 倍速上限。超过 2.0 在绝大多数平台会被判定为异常播放行为
SPEED_MIN, SPEED_MAX = 0.5, 2.0


class ConfigError(Exception):
    pass


# 各 channel 对应的可执行文件名，用于在目录里定位主程序
_BROWSER_EXES = {
    "chrome": ("chrome.exe",),
    "msedge": ("msedge.exe",),
    "chromium": ("chrome.exe", "chromium.exe"),
}


# 各浏览器的常见安装位置，用于自动探测
_BROWSER_LOCATIONS: dict[str, tuple[str, ...]] = {
    "chrome": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ),
    "msedge": (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    ),
}


def find_installed_browser(channel: str) -> str | None:
    """返回该 channel 的浏览器路径，没装则返回 None。"""
    for raw in _BROWSER_LOCATIONS.get(channel, ()):
        path = Path(os.path.expandvars(raw))
        if path.is_file():
            return str(path)
    return None


def detect_browser() -> tuple[str, str | None]:
    """自动挑一个本机装了的浏览器，返回 (channel, 路径)。

    存在的意义很实际：默认写死 chrome，而不少 Windows 机器只有 Edge，
    这种情况下用户一点"开始"就报错，却看不出是浏览器没装。
    """
    for channel in ("chrome", "msedge"):
        found = find_installed_browser(channel)
        if found:
            return channel, found
    # 都没找到就交给 Playwright 自己去找它下载的内核
    return "chromium", None


def resolve_browser_path(raw: str, channel: str = "chrome") -> str | None:
    """把用户填的路径解析成真正的可执行文件。

    接受三种写法，因为这三种用户都可能填：
      - 完整 exe 路径      C:\\...\\Application\\chrome.exe
      - 安装目录          C:\\Program Files\\Google\\Chrome\\Application
      - 上一级目录        C:\\Program Files\\Google\\Chrome
    """
    if not raw:
        return None
    path = Path(raw)

    if path.is_file():
        return str(path)

    if path.is_dir():
        names = _BROWSER_EXES.get(channel, ("chrome.exe", "msedge.exe"))
        # 先在本层找，再往下找一层（Chrome 的 exe 在 Application 子目录里）
        for name in names:
            direct = path / name
            if direct.is_file():
                return str(direct)
        for name in names:
            for found in path.glob(f"*/{name}"):
                if found.is_file():
                    return str(found)
        return None

    # 路径压根不存在，交回原值，让 Playwright 给出明确报错
    return str(path)


@dataclass
class Config:
    path: Path
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __init__(self, path: str | Path = "config.toml"):
        self.path = Path(path)
        if not self.path.exists():
            raise ConfigError(
                f"未找到配置文件 {self.path}，"
                f"请复制 config.example.toml 为 {self.path.name} 后填写。"
            )
        self._raw = {}
        self.reload()

    def reload(self) -> None:
        try:
            with self.path.open("rb") as fp:
                self._raw = tomllib.load(fp)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"配置文件格式错误：{exc}") from exc

    def _get(self, section: str, key: str, default: Any = None) -> Any:
        return self._raw.get(section, {}).get(key, default)

    def _get_live(self, section: str, key: str, default: Any = None) -> Any:
        """读取时重新解析文件，实现运行中改配置立即生效。"""
        self.reload()
        return self._get(section, key, default)

    # ---------- 账号 ----------
    @property
    def username(self) -> str:
        return str(self._get("account", "username", "")).strip()

    @property
    def password(self) -> str:
        return str(self._get("account", "password", "")).strip()

    # ---------- 浏览器 ----------
    @property
    def channel(self) -> str:
        """浏览器 channel。auto 表示由程序探测本机装了什么。"""
        ch = str(self._get("browser", "channel", "auto")).strip().lower()
        if ch in ("", "auto"):
            return detect_browser()[0]
        # Playwright 里 Edge 的 channel 名是 msedge
        return "msedge" if ch == "edge" else ch

    @property
    def channel_raw(self) -> str:
        """配置里原样写的值，界面回填要用它才能显示"自动"。"""
        return str(self._get("browser", "channel", "auto")).strip().lower() or "auto"

    @property
    def executable_path(self) -> str | None:
        """浏览器可执行文件路径。

        Playwright 要的是精确到 .exe 的路径，但"填浏览器安装位置"这种理解
        太自然了，所以这里兼容：给了文件夹就在里面找浏览器主程序。
        """
        raw = str(self._get("browser", "executable_path", "")).strip().strip('"')
        if not raw:
            return None
        return resolve_browser_path(raw, self.channel)

    @property
    def window_size(self) -> tuple[int, int]:
        size = self._get("browser", "window_size", [1440, 900])
        try:
            return int(size[0]), int(size[1])
        except (TypeError, ValueError, IndexError):
            return 1440, 900

    @property
    def maximize(self) -> bool:
        """浏览器是否最大化打开。

        默认开启：小窗刷课不仅难看，有些平台的播放器在窄视口下
        会切成移动端布局，选择器全对不上。
        """
        return bool(self._get("browser", "maximize", True))

    @property
    def keep_browser_open(self) -> bool:
        """一节都没学成时，是否保留浏览器窗口。

        默认开启：失败时立刻关窗，用户只会看到"闪一下就没了"，
        既看不到出错的页面，也无法手动接管。
        """
        return bool(self._get_live("browser", "keep_open_on_failure", True))

    @property
    def headless(self) -> bool:
        return bool(self._get("browser", "headless", False))

    # ---------- 课程 ----------
    @property
    def course_items(self) -> list[dict]:
        """课程列表，每项含 url 与 note。

        新格式是 [[course.list]] 表数组（可带备注）；
        老配置里是 urls = [...] 纯字符串数组，这里一并兼容，
        免得升级后用户的课程地址凭空消失。
        """
        raw = self._get("course", "list", []) or []
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url", "")).strip()
            if url.startswith("http"):
                items.append({"url": url, "note": str(entry.get("note", "")).strip()})
        if items:
            return items
        legacy = self._get("course", "urls", []) or []
        return [{"url": u.strip(), "note": ""} for u in legacy
                if isinstance(u, str) and u.strip().startswith("http")]

    @property
    def course_urls(self) -> list[str]:
        return [i["url"] for i in self.course_items]

    @property
    def speed(self) -> float:
        """热重载：跑着的时候改倍速立刻生效。"""
        try:
            raw = float(self._get_live("course", "speed", 1.0))
        except (TypeError, ValueError):
            raw = 1.0
        return min(max(raw, SPEED_MIN), SPEED_MAX)

    @property
    def mute(self) -> bool:
        return bool(self._get_live("course", "mute", True))

    @property
    def limit_max_minutes(self) -> float:
        """热重载：可以跑到一半决定提前收工。"""
        try:
            return max(0.0, float(self._get_live("course", "limit_max_minutes", 0)))
        except (TypeError, ValueError):
            return 0.0

    # ---------- 答题 ----------
    @property
    def answer_enabled(self) -> bool:
        return bool(self._get_live("answer", "enabled", True))

    @property
    def auto_submit(self) -> bool:
        """默认 false：只填不交。AI 正确率不做保证，交不交由使用者决定。"""
        return bool(self._get_live("answer", "auto_submit", False))

    @property
    def retry_until_correct(self) -> bool:
        """答错是否继续换答案重试，直到平台判定正确。

        默认开启：答错的题平台会重新弹出，只答一次会导致同一题无限重弹，
        视频永远播不下去。试错才能真正把播放推进下去。
        注意开启后必然会提交答案——不提交就拿不到对错反馈。
        """
        return bool(self._get_live("answer", "retry_until_correct", True))

    @property
    def answer_provider(self) -> str:
        return str(self._get("answer", "provider", "anthropic")).strip().lower()

    @property
    def api_key(self) -> str:
        key = str(self._get("answer", "api_key", "")).strip()
        if key:
            return key
        # 配置留空时回落到环境变量，避免把密钥写进文件
        env = "ANTHROPIC_API_KEY" if self.answer_provider == "anthropic" else "OPENAI_API_KEY"
        return os.environ.get(env, "").strip()

    @property
    def model(self) -> str:
        return str(self._get("answer", "model", "claude-sonnet-5")).strip()

    @property
    def base_url(self) -> str:
        return str(self._get("answer", "base_url", "")).strip()

    @property
    def answer_timeout(self) -> float:
        try:
            return float(self._get("answer", "timeout", 45))
        except (TypeError, ValueError):
            return 45.0

    @property
    def answer_cache(self) -> bool:
        return bool(self._get("answer", "cache", True))

    # ---------- 运行时 ----------
    @property
    def log_level(self) -> str:
        return str(self._get("runtime", "log_level", "INFO")).strip().upper()

    @property
    def font_size(self) -> int:
        """界面字号。范围夹在 10~24，超出会把布局撑坏。"""
        try:
            return max(10, min(24, int(self._get("runtime", "font_size", 15))))
        except (TypeError, ValueError):
            return 15

    @property
    def proxy(self) -> str:
        """AI 调用走的代理，形如 http://127.0.0.1:7890。

        只影响调用大模型，不影响刷课用的浏览器——浏览器用的是系统代理。
        """
        return str(self._get("runtime", "proxy", "")).strip()

    @property
    def on_finish(self) -> str:
        """全部刷完后做什么：none / quit / sleep / shutdown。"""
        v = str(self._get("runtime", "on_finish", "none")).strip().lower()
        return v if v in ("none", "quit", "sleep", "shutdown") else "none"

    @property
    def always_on_top(self) -> bool:
        return bool(self._get("runtime", "always_on_top", False))

    @property
    def start_minimized(self) -> bool:
        """开机自启时是否直接最小化，不弹到面前。"""
        return bool(self._get("runtime", "start_minimized", False))

    @property
    def autorun(self) -> bool:
        """打开软件后是否自动开始刷课。"""
        return bool(self._get("runtime", "autorun", False))

    @property
    def beep_on_captcha(self) -> bool:
        return bool(self._get("runtime", "beep_on_captcha", True))

    def validate(self) -> list[str]:
        """返回问题清单，空列表表示配置可用。"""
        problems: list[str] = []
        if not self.course_urls:
            problems.append("course.urls 为空，请至少填写一个课程播放页地址。")
        if self.answer_enabled and not self.api_key:
            problems.append(
                "answer.enabled = true 但未配置 api_key"
                "（也可设置环境变量 ANTHROPIC_API_KEY / OPENAI_API_KEY），"
                "否则遇题只能暂停等你处理。"
            )
        if self.answer_provider == "openai_compatible" and not self.base_url:
            problems.append("provider = openai_compatible 时必须填写 base_url。")
        return problems
