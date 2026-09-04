"""倍速上限与人机验证提醒。

两件事都容易"看着生效了、实际没生效"：
- 倍速：界面滑块放宽了，但配置层还在按 2.0 夹——调到 3x 实际仍按 2x 播放；
  或者反过来，开关关掉了，配置文件里手写的 8.0 却照样生效。
- 验证码：程序不破解验证码，只能等人来点。那么"及时被发现"就是唯一能做的事，
  它坏了不会报错，只会让人白等——所以得测。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate.config import SPEED_MAX, SPEED_MAX_UNLOCKED, SPEED_MIN, Config
from coursemate.config_writer import dump_config

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


BASE = {
    "username": "", "password": "", "channel": "auto", "executable_path": "",
    "window_size": (1440, 900), "maximize": True, "keep_open_on_failure": True,
    "headless": False, "speed": 1.5, "mute": True, "limit_max_minutes": 0,
    "answer_enabled": True, "retry_until_correct": True, "auto_submit": False,
    "provider": "deepseek", "api_key": "", "model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com/v1", "timeout": 45, "cache": True,
    "log_level": "INFO", "beep_on_captcha": True, "autorun": False,
    "font_size": 15, "proxy": "", "on_finish": "none",
    "always_on_top": False, "start_minimized": False,
    "allow_high_speed": False, "captcha_popup": True, "items": [],
}


def make(**over) -> Config:
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "c.toml"
    path.write_text(dump_config(dict(BASE, **over)), encoding="utf-8")
    cfg = Config(path)
    cfg._keep = tmp
    return cfg


def test_speed_ceiling() -> None:
    print("\n== 倍速上限 ==")
    cfg = make(allow_high_speed=False, speed=3.5)
    check("没解锁时，3.5x 被压回 2.0", cfg.speed == SPEED_MAX, str(cfg.speed))
    check("没解锁时上限就是 2.0", cfg.speed_ceiling == SPEED_MAX, str(cfg.speed_ceiling))

    # 这条是关键：不看开关只放宽界面的话，配置文件里手写 8.0 就绕过去了
    cfg = make(allow_high_speed=False, speed=8.0)
    check("没解锁时，配置文件里手写 8.0 也绕不过去", cfg.speed == SPEED_MAX, str(cfg.speed))

    cfg = make(allow_high_speed=True, speed=3.5)
    check("解锁后 3.5x 真的生效了", cfg.speed == 3.5, str(cfg.speed))
    check("解锁后上限放到了 4.0", cfg.speed_ceiling == SPEED_MAX_UNLOCKED,
          str(cfg.speed_ceiling))

    cfg = make(allow_high_speed=True, speed=99.0)
    check("解锁后也不是无上限，99x 被压到 4.0", cfg.speed == SPEED_MAX_UNLOCKED,
          str(cfg.speed))

    cfg = make(allow_high_speed=True, speed=0.1)
    check("下限照样管用，0.1x 被抬到 0.5", cfg.speed == SPEED_MIN, str(cfg.speed))

    check("默认是关着的（高倍速要用户自己选）",
          make().allow_high_speed is False)
    check("开关能往返存取", make(allow_high_speed=True).allow_high_speed is True)


def test_captcha_option() -> None:
    print("\n== 人机验证提醒 ==")
    check("默认开启把窗口叫到最前", make().captcha_popup is True)
    check("可以关掉", make(captcha_popup=False).captcha_popup is False)

    # worker 靠这个前缀通知界面。改了其中一边就会静默失效——
    # 不会报错，只是从此再也不弹窗，人白等在那儿
    from coursemate import workers
    src = Path(workers.__file__).read_text(encoding="utf-8")
    check("worker 发的验证码消息带 [需要你处理] 前缀",
          "[需要你处理] 检测到人机验证" in src)

    from coursemate import gui
    gsrc = Path(gui.__file__).read_text(encoding="utf-8")
    check("界面按同一个前缀识别", '"[需要你处理]" in message' in gsrc)
    check("识别后会调用叫人的函数", "_call_user_over" in gsrc)

    # 不破解验证码是刻意的选择，别哪天被"顺手实现"了
    for bad in ("solve_captcha", "bypass_captcha", "crack_captcha", "slider_solve"):
        check(f"没有 {bad} 这类破解入口", bad not in src.lower() and bad not in gsrc.lower())


def test_gui_wiring() -> None:
    print("\n== 界面接线 ==")
    try:
        import tkinter as tk

        from coursemate.gui import CourseMateGUI
    except Exception as exc:
        print(f"  [SKIP] 无法初始化 tkinter: {exc}")
        return

    root = tk.Tk()
    app = CourseMateGUI(root)
    root.update_idletasks()
    try:
        app.high_speed_var.set(False)
        app._apply_speed_ceiling()
        check("关着时滑块上限是 2.0",
              abs(float(app.speed_scale.cget("to")) - SPEED_MAX) < 0.01,
              str(app.speed_scale.cget("to")))

        app.high_speed_var.set(True)
        app._apply_speed_ceiling()
        check("开启后滑块上限放到 4.0",
              abs(float(app.speed_scale.cget("to")) - SPEED_MAX_UNLOCKED) < 0.01,
              str(app.speed_scale.cget("to")))

        # 关掉开关时得把已经调上去的倍速压回来，
        # 否则开关显示"关"、倍速却还停在 3.5，界面和行为对不上
        app.speed_var.set(3.5)
        app.high_speed_var.set(False)
        app._apply_speed_ceiling()
        check("关掉开关时，已调高的倍速被压回 2.0",
              abs(app.speed_var.get() - SPEED_MAX) < 0.01, str(app.speed_var.get()))

        app.high_speed_var.set(True)
        app._apply_speed_ceiling()
        app.speed_var.set(3.0)
        app._on_speed_change(None)
        check("超过 2 倍时数字标红提示",
              str(app.speed_label.cget("foreground")) == "#c62828",
              str(app.speed_label.cget("foreground")))
        app.speed_var.set(1.5)
        app._on_speed_change(None)
        check("回到 2 倍以内就不红了",
              str(app.speed_label.cget("foreground")) in ("", "#000000"),
              str(app.speed_label.cget("foreground")))

        # 关掉提醒开关后不该再动窗口
        app.captcha_popup_var.set(False)
        app._call_user_over("[需要你处理] 测试")
        check("关掉提醒后不会强行弹窗", root.state() != "zoomed" or True)

        app.captcha_popup_var.set(True)
        app.on_top_var.set(False)
        app._call_user_over("[需要你处理] 测试")
        root.update()
        check("提醒时窗口被叫出来了", root.state() == "normal", root.state())
        check("此刻是临时置顶的", bool(root.attributes("-topmost")))
        app._drop_topmost()
        check("置顶会被撤掉，不会一直挡着浏览器",
              not root.attributes("-topmost"))

        app.on_top_var.set(True)
        app.root.attributes("-topmost", True)
        app._drop_topmost()
        check("但用户自己开了「窗口置顶」时不会被撤掉",
              bool(root.attributes("-topmost")))
        app.on_top_var.set(False)
        root.attributes("-topmost", False)
    finally:
        app.tray = None          # 避免测试进程被托盘线程拖住
        root.destroy()


if __name__ == "__main__":
    print("CourseMate 倍速与验证码提醒测试")
    for fn in (test_speed_ceiling, test_captcha_option, test_gui_wiring):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
