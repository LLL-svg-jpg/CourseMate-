"""配置写回。

标准库的 tomllib 只能读不能写，而 GUI 必须能保存用户填的内容。
这里不引入第三方 TOML 库（本机装不了包），而是按固定模板生成——
配置项是我们自己定义的、有限且已知的，模板生成比通用序列化更可控，
还能顺便把注释一起写回去，让文件保持可手工编辑。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

TEMPLATE = '''# CourseMate 配置
# 本文件由图形界面生成，也可直接手工编辑。
# 注意：这里可能保存了你的账号密码与 API Key，不要分享给他人。

[account]
# 留空则运行时在浏览器里手动登录，程序会自动保存 Cookie，下次免密
username = "{username}"
password = "{password}"

[browser]
# auto / chrome / edge / chromium。auto = 自动使用本机已装的浏览器
channel = "{channel}"
# 浏览器可执行文件路径，留空则自动查找
executable_path = "{executable_path}"
# 最大化打开浏览器。关掉才会用下面的 window_size
maximize = {maximize}
window_size = [{win_w}, {win_h}]
# 一节都没学成时保留浏览器窗口，方便你看清是哪一步不对。
# 关掉它，失败时窗口会一闪而过，什么都看不到
keep_open_on_failure = {keep_open}
# 无头模式。建议 false —— 陪伴软件的意义就是你能看见它在干活
headless = {headless}

[course]
# 播放倍速，程序会强制夹在 [0.5, 2.0]
speed = {speed}
# 是否静音
mute = {mute}
# 单门课程最长学习分钟数，0 表示不限制。人机验证与答题耗时不计入
limit_max_minutes = {limit_max_minutes}

# 课程列表，按顺序依次学习。note 是给你自己看的备注，程序不使用
{course_list}
[answer]
# 是否启用自动答题。关闭则遇到弹题仅暂停并提醒
enabled = {answer_enabled}
# 答错后是否继续换答案重试，直到平台判定正确。
# 开启后必然会提交答案——不提交就拿不到对错反馈。
# 关闭则退回"只填一次不提交"的保守模式。
retry_until_correct = {retry_until_correct}
# 仅在 retry_until_correct = false 时生效：是否提交那唯一一次作答
auto_submit = {auto_submit}
# anthropic / deepseek / qwen / zhipu / moonshot / doubao
# / siliconflow / baichuan / minimax / openai_compatible
provider = "{provider}"
# API Key。留空则回落到环境变量 ANTHROPIC_API_KEY / OPENAI_API_KEY
api_key = "{api_key}"
model = "{model}"
# 除 anthropic 外都必填。图形界面选服务商时会自动带出
base_url = "{base_url}"
timeout = {timeout}
# 本地题库缓存。同门课题目高度重复，开启后 AI 调用量大幅下降
cache = {cache}

[runtime]
log_level = "{log_level}"
beep_on_captcha = {beep_on_captcha}
# 打开软件后自动开始刷课
autorun = {autorun}
# 界面字号（10~24）
font_size = {font_size}
# AI 调用走的代理，例如 http://127.0.0.1:7890。只影响调大模型，不影响浏览器
proxy = "{proxy}"
# 全部刷完后做什么：none 什么都不做 / quit 退出程序 / sleep 睡眠 / shutdown 关机
on_finish = "{on_finish}"
# 窗口置顶
always_on_top = {always_on_top}
# 开机自启时直接最小化，不弹到面前
start_minimized = {start_minimized}
'''


def _esc(value: Any) -> str:
    """转义 TOML 基本字符串。密码和 Key 里出现反斜杠或引号并不罕见。"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _bool(value: Any) -> str:
    return "true" if value else "false"


def dump_config(data: dict[str, Any]) -> str:
    # 课程列表写成 TOML 表数组，每门课带一条自己的备注。
    # 老配置只有 urls 字符串数组，这里一并接受，转成同样的结构。
    items = data.get("items")
    if items is None:
        items = [{"url": u, "note": ""} for u in (data.get("urls") or [])]
    blocks = [
        f'[[course.list]]\nurl = "{_esc(it.get("url", ""))}"\n'
        f'note = "{_esc(it.get("note", ""))}"\n'
        for it in items
    ]
    course_list = "\n".join(blocks) if blocks else ""
    win = data.get("window_size") or (1440, 900)
    return TEMPLATE.format(
        username=_esc(data.get("username", "")),
        password=_esc(data.get("password", "")),
        channel=_esc(data.get("channel", "auto")),
        executable_path=_esc(data.get("executable_path", "")),
        maximize=_bool(data.get("maximize", True)),
        win_w=int(win[0]),
        win_h=int(win[1]),
        keep_open=_bool(data.get("keep_open_on_failure", True)),
        headless=_bool(data.get("headless", False)),
        course_list=course_list,
        speed=float(data.get("speed", 1.5)),
        mute=_bool(data.get("mute", True)),
        limit_max_minutes=float(data.get("limit_max_minutes", 0)),
        answer_enabled=_bool(data.get("answer_enabled", True)),
        retry_until_correct=_bool(data.get("retry_until_correct", True)),
        auto_submit=_bool(data.get("auto_submit", False)),
        provider=_esc(data.get("provider", "anthropic")),
        api_key=_esc(data.get("api_key", "")),
        model=_esc(data.get("model", "claude-opus-5")),
        base_url=_esc(data.get("base_url", "")),
        timeout=float(data.get("timeout", 45)),
        cache=_bool(data.get("cache", True)),
        log_level=_esc(data.get("log_level", "INFO")),
        autorun=_bool(data.get("autorun", False)),
        font_size=int(data.get("font_size", 15)),
        proxy=_esc(data.get("proxy", "")),
        on_finish=_esc(data.get("on_finish", "none")),
        always_on_top=_bool(data.get("always_on_top", False)),
        start_minimized=_bool(data.get("start_minimized", False)),
        beep_on_captcha=_bool(data.get("beep_on_captcha", True)),
    )


def save_config(path: str | Path, data: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config(data), encoding="utf-8")
    return path
