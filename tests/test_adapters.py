"""适配器结构测试（不需要真实 playwright）。

本机无法访问 PyPI，装不上 playwright，但适配器的导入链、注册机制、
URL 路由和抽象契约完整性都是纯 Python 逻辑，可以用桩模块验证。

真正需要浏览器的部分（选择器是否命中真实 DOM）无法在这里覆盖，
必须在装好依赖、拿到真实课程页后用 --check 与实跑验证。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def install_playwright_stub() -> None:
    """注入最小 playwright 桩，只提供类型名，不提供行为。"""
    if "playwright" in sys.modules:
        return
    pkg = types.ModuleType("playwright")
    async_api = types.ModuleType("playwright.async_api")

    class _Stub:
        def __init__(self, *a, **k): ...

    for name in (
        "Page", "Frame", "BrowserContext", "Browser", "Playwright",
        "ElementHandle", "Locator", "TimeoutError",
    ):
        setattr(async_api, name, type(name, (_Stub,), {}))
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]

    pkg.async_api = async_api  # type: ignore[attr-defined]
    sys.modules["playwright"] = pkg
    sys.modules["playwright.async_api"] = async_api


def test_registry() -> None:
    print("\n== 平台注册与路由 ==")
    from coursemate.platforms import resolve, supported_platforms

    names = supported_platforms()
    check("已注册两个平台", len(names) == 2, f"names={names}")
    check("包含智慧树", "知道智慧树" in names)
    check("包含超星学习通", "超星学习通" in names)

    cases = [
        ("https://studyh5.zhihuishu.com/videoStudy.html#/study", "知道智慧树"),
        ("https://hike.zhihuishu.com/course/1", "知道智慧树"),
        ("https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=1", "超星学习通"),
        ("https://mooc2-ans.chaoxing.com/mooc2/x", "超星学习通"),
    ]
    for url, expect in cases:
        got = resolve(url)
        check(f"路由 {url[:44]}", got is not None and got.name == expect,
              f"got={got.name if got else None}")

    check("无关 URL 不误认领", resolve("https://www.bilibili.com/video/x") is None)
    check("空串不崩溃", resolve("") is None)


def test_contract() -> None:
    print("\n== 抽象契约完整性 ==")
    from coursemate.platforms.base import PlatformAdapter
    from coursemate.platforms.chaoxing import ChaoxingAdapter
    from coursemate.platforms.zhihuishu import ZhihuishuAdapter

    required = [
        "match", "is_logged_in", "login", "open_course", "list_lessons",
        "enter_lesson", "get_progress", "lesson_finished",
        "detect_question", "extract_questions", "fill_answer",
        "submit_answer", "close_question", "detect_captcha", "captcha_cleared",
        "video_frame", "prepare_page",
    ]
    for cls in (ZhihuishuAdapter, ChaoxingAdapter):
        missing = [m for m in required if not hasattr(cls, m)]
        check(f"{cls.name} 实现全部契约方法", not missing, f"missing={missing}")
        # 抽象方法必须被真正覆盖，而不是继承基类的抽象声明
        unimplemented = [
            m for m in ("is_logged_in", "login", "open_course", "list_lessons",
                        "enter_lesson", "get_progress", "lesson_finished")
            if getattr(cls, m) is getattr(PlatformAdapter, m, None)
        ]
        check(f"{cls.name} 抽象方法均已覆盖", not unimplemented, f"{unimplemented}")
        check(f"{cls.name} 可实例化", isinstance(cls(), PlatformAdapter))


def test_question_typing() -> None:
    print("\n== 题型推断 ==")
    from coursemate.answer.base import Option
    from coursemate.platforms.zhihuishu import ZhihuishuAdapter

    guess = ZhihuishuAdapter._guess_type
    opts2 = [Option("A", "正确"), Option("B", "错误")]
    opts4 = [Option(chr(65 + i), f"选项{i}") for i in range(4)]

    check("判断题按选项识别", guess("地球是圆的", opts2) == "judge")
    check("判断题按题干识别", guess("判断题：地球是方的", opts4) == "judge")
    check("多选题按题干识别", guess("（多选）以下哪些正确", opts4) == "multiple")
    check("默认单选", guess("以下哪个正确", opts4) == "single")
    check("无选项视为填空", guess("请填写答案", []) == "fill")


def test_worker_error_classify() -> None:
    print("\n== 协程异常分类 ==")
    from coursemate.workers import is_closed, is_expected

    check("识别浏览器关闭", is_closed(Exception("Target page, context or browser has been closed")))
    check("识别连接关闭", is_closed(Exception("Connection closed")))
    check("普通异常不算关闭", not is_closed(Exception("boom")))
    check("识别选择器未命中", is_expected(Exception("waiting for selector \".x\"")))
    check("识别 frame 脱离", is_expected(Exception("frame was detached")))
    check("真实错误不被吞掉", not is_expected(Exception("ZeroDivisionError")))


if __name__ == "__main__":
    print("CourseMate 适配器结构测试（playwright 使用桩模块）")
    install_playwright_stub()
    for fn in (test_registry, test_contract, test_question_typing, test_worker_error_classify):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
