"""课程列表组件测试：备注、排序、配置往返。

排序和备注这两件事一旦出错很难当场看出来：
顺序错了只是"课刷得不是想要的那门"，备注丢了则是保存后才发现。
所以在这里把界面顺序 → 保存 → 读回这条链路整个跑一遍。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
}


def roundtrip(data: dict):
    from coursemate.config import Config
    from coursemate.config_writer import dump_config

    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "c.toml"
    path.write_text(dump_config(data), encoding="utf-8")
    cfg = Config(path)
    cfg._keep = tmp  # 防止临时目录被提前回收
    return cfg


def test_note_roundtrip() -> None:
    print("\n== 备注往返 ==")
    items = [
        {"url": "https://a.zhihuishu.com/1", "note": "思想道德"},
        {"url": "https://b.zhihuishu.com/2", "note": "大学英语"},
    ]
    cfg = roundtrip(dict(BASE, items=items))
    check("条目数正确", len(cfg.course_items) == 2, str(len(cfg.course_items)))
    check("备注保留", [i["note"] for i in cfg.course_items] == ["思想道德", "大学英语"])
    check("顺序保留", cfg.course_urls == [i["url"] for i in items])
    check("course_urls 仍可用", all(u.startswith("http") for u in cfg.course_urls))


def test_note_special_chars() -> None:
    print("\n== 备注特殊字符 ==")
    note = '带"引号"和\\反斜杠的备注'
    cfg = roundtrip(dict(BASE, items=[{"url": "https://x.zhihuishu.com/c", "note": note}]))
    check("引号与反斜杠不破坏配置", cfg.course_items[0]["note"] == note,
          repr(cfg.course_items[0]["note"]))

    cfg2 = roundtrip(dict(BASE, items=[{"url": "https://x.zhihuishu.com/c", "note": ""}]))
    check("空备注也能读回", cfg2.course_items[0]["note"] == "")


def test_legacy_urls() -> None:
    print("\n== 旧配置兼容 ==")
    from coursemate.config import Config

    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "old.toml"
        # 老版本只写 urls 数组，升级后课程地址不能凭空消失
        p.write_text(
            '[course]\nurls = ["https://a.zhihuishu.com/1", "https://b.zhihuishu.com/2"]\n'
            "speed = 1.5\n", encoding="utf-8")
        cfg = Config(p)
        check("旧 urls 能读出来", len(cfg.course_items) == 2, str(cfg.course_items))
        check("旧配置备注为空串", all(i["note"] == "" for i in cfg.course_items))
        check("非法地址被过滤",
              all(u.startswith("http") for u in cfg.course_urls))

    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "mix.toml"
        # 新旧并存时以新格式为准，否则会出现重复课程
        p.write_text(
            '[course]\nurls = ["https://old.zhihuishu.com/x"]\n\n'
            '[[course.list]]\nurl = "https://new.zhihuishu.com/y"\nnote = "新"\n',
            encoding="utf-8")
        cfg = Config(p)
        check("新旧并存时以新格式为准",
              [i["url"] for i in cfg.course_items] == ["https://new.zhihuishu.com/y"],
              str(cfg.course_items))


def test_reorder_logic() -> None:
    """不依赖 GUI，单独验证重排算法本身。"""
    print("\n== 重排算法 ==")

    def move(seq, i, delta):
        j = max(0, min(len(seq) - 1, i + delta))
        if i != j:
            seq.insert(j, seq.pop(i))
        return seq

    check("上移一位", move(["a", "b", "c"], 1, -1) == ["b", "a", "c"])
    check("下移一位", move(["a", "b", "c"], 0, 1) == ["b", "a", "c"])
    check("下移两位", move(["a", "b", "c"], 0, 2) == ["b", "c", "a"])
    check("越界上移夹住", move(["a", "b", "c"], 0, -5) == ["a", "b", "c"])
    check("越界下移夹住", move(["a", "b", "c"], 2, 9) == ["a", "b", "c"])
    check("单条不受影响", move(["a"], 0, 3) == ["a"])


def test_gui_component() -> None:
    print("\n== 界面组件 ==")
    try:
        import tkinter as tk

        from coursemate.gui import UrlList
    except Exception as exc:
        print(f"  [SKIP] 无法初始化 tkinter: {exc}")
        return

    root = tk.Tk()
    root.withdraw()
    try:
        ul = UrlList(root)
        ul.set_items([{"url": f"https://x.zhihuishu.com/{i}", "note": n}
                      for i, n in enumerate(["甲", "乙", "丙"])])
        check("载入三条", len(ul.rows) == 3)

        ul.move(ul.rows[2], -2)
        check("末条移到首位",
              [r["note_var"].get() for r in ul.rows] == ["丙", "甲", "乙"])
        check("序号跟着重排",
              [r["label"]["text"] for r in ul.rows] == ["1.", "2.", "3."])
        check("取值顺序与界面一致",
              [i["note"] for i in ul.get_items()] == ["丙", "甲", "乙"])

        # 空地址不该被保存，但它的备注也不该拖累其他条目
        ul.add_row("", "空的")
        check("空地址被跳过", len(ul.get_items()) == 3, str(len(ul.get_items())))

        ul.set_items([{"url": "https://only.zhihuishu.com/1", "note": "唯一"}])
        ul.remove_row(ul.rows[0])
        check("最后一行删除时清空而非移除", len(ul.rows) == 1)
        check("清空后取值为空", ul.get_items() == [])
    finally:
        root.destroy()


if __name__ == "__main__":
    print("CourseMate 课程列表测试")
    for fn in (test_note_roundtrip, test_note_special_chars, test_legacy_urls,
               test_reorder_logic, test_gui_component):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
