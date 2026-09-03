"""试错答题测试。

这是最需要验证的一块：
- 试错序列排错了，会白白多点很多次，触发平台风控
- 循环退出条件写错了，程序会永远点下去，视频彻底卡死
两种失败在真实刷课时都很难当场看出来，只能靠测试兜住。
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def install_stub() -> None:
    if "playwright" in sys.modules:
        return
    pkg = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    for n in ("Page", "Frame", "BrowserContext", "Browser", "Playwright",
              "ElementHandle", "Locator", "TimeoutError"):
        setattr(api, n, type(n, (object,), {}))
    api.async_playwright = lambda: None  # type: ignore[attr-defined]
    pkg.async_api = api  # type: ignore[attr-defined]
    sys.modules["playwright"] = pkg
    sys.modules["playwright.async_api"] = api


def make_q(n_options: int = 4, qtype: str = "single"):
    from coursemate.answer.base import Option, Question
    return Question(
        stem="测试题干",
        options=[Option(chr(ord("A") + i), f"选项{chr(ord('A') + i)}") for i in range(n_options)],
        qtype=qtype,
    )


def test_single_order() -> None:
    print("\n== 单选尝试序列 ==")
    from coursemate.answer.strategy import build_attempts

    q = make_q(4, "single")
    attempts = build_attempts(q, ["C"])
    check("AI 答案排第一", attempts[0] == ["C"], str(attempts[:2]))
    check("覆盖全部 4 个选项", len(attempts) == 4, str(attempts))
    flat = [a[0] for a in attempts]
    check("无重复", len(set(flat)) == 4, str(flat))

    no_ai = build_attempts(q, [])
    check("无 AI 答案时按页面顺序", [a[0] for a in no_ai] == ["A", "B", "C", "D"], str(no_ai))

    bad_ai = build_attempts(q, ["Z"])
    check("越界的 AI 答案被忽略", [a[0] for a in bad_ai] == ["A", "B", "C", "D"])


def test_judge() -> None:
    print("\n== 判断题 ==")
    from coursemate.answer.base import Option, Question
    from coursemate.answer.strategy import build_attempts

    q = Question(stem="地球是圆的", qtype="judge",
                 options=[Option("A", "正确"), Option("B", "错误")])
    attempts = build_attempts(q, ["B"])
    check("最多两次尝试", len(attempts) == 2, str(attempts))
    check("AI 答案优先", attempts[0] == ["B"], str(attempts))


def test_multiple_order() -> None:
    print("\n== 多选尝试序列 ==")
    from coursemate.answer.strategy import MAX_ATTEMPTS, build_attempts

    q = make_q(4, "multiple")
    attempts = build_attempts(q, ["A", "B"])
    check("AI 答案排第一", attempts[0] == ["A", "B"], str(attempts[0]))

    head = [set(a) for a in attempts[:6]]
    check("紧随其后是差一个的组合",
          {"A"} in head and {"B"} in head and {"A", "B", "C"} in head,
          str(head))
    check("尝试项互不重复",
          len({tuple(sorted(a)) for a in attempts}) == len(attempts))
    check("不超过上限", len(attempts) <= MAX_ATTEMPTS, str(len(attempts)))

    # 选项多时组合爆炸，必须被夹住
    big = build_attempts(make_q(6, "multiple"), ["A"])
    check("6 选项时被上限夹住", len(big) == MAX_ATTEMPTS, str(len(big)))
    check("每次尝试都非空", all(len(a) > 0 for a in big))


def test_no_options() -> None:
    print("\n== 边界情况 ==")
    from coursemate.answer.base import Question
    from coursemate.answer.strategy import build_attempts

    check("无选项返回空序列", build_attempts(Question(stem="填空", qtype="fill")) == [])
    single_opt = build_attempts(make_q(1, "single"), [])
    check("只有一个选项也能生成", single_opt == [["A"]], str(single_opt))


# ---------------- 试错循环 ----------------

class FakeAdapter:
    """模拟平台：只有选中 correct_answer 才判定正确。"""

    def __init__(self, correct: set[str], max_rounds: int = 100,
                 feedback_override: str | None = None):
        self.correct = correct
        self.selected: set[str] = set()
        self.submits = 0
        self.closed = False
        self.confirmed = False
        self.question_open = True
        self.feedback_override = feedback_override
        self.max_rounds = max_rounds

    async def detect_question(self, page): return self.question_open

    async def clear_selection(self, page, question): self.selected.clear()

    async def fill_answer(self, page, question, keys, result):
        self.selected = set(keys)
        return True

    async def submit_answer(self, page, auto_submit):
        self.submits += 1
        if self.submits > self.max_rounds:
            raise AssertionError("提交次数失控，说明循环没有退出条件")
        return True

    async def read_feedback(self, page):
        if self.feedback_override:
            return self.feedback_override
        if self.selected == self.correct:
            self.question_open = False
            return "correct"
        return "wrong"

    async def confirm_and_close(self, page):
        self.confirmed = True
        self.question_open = False
        return True

    async def close_question(self, page):
        self.closed = True
        self.question_open = False


class FakeCache:
    def __init__(self): self.stored = []
    def get(self, q): return None
    def put(self, q, r): self.stored.append(r)


def run_solve(adapter, question, ai_keys, retry=True):
    from coursemate.answer.base import AnswerResult
    from coursemate.workers import solve_one_question

    class Cfg:
        answer_cache = True
        retry_until_correct = retry
        auto_submit = False

    class Provider:
        async def solve(self, q):
            return AnswerResult(option_keys=list(ai_keys), confidence=0.8, source="ai")

    cache = FakeCache()
    ok = asyncio.run(solve_one_question(
        None, adapter, Cfg(), question, Provider() if ai_keys is not None else None, cache))
    return ok, cache


def test_retry_loop() -> None:
    print("\n== 试错循环 ==")
    q = make_q(4, "single")

    # AI 一次答对
    a1 = FakeAdapter(correct={"C"})
    ok, cache = run_solve(a1, q, ["C"])
    check("AI 答对时只提交一次", ok and a1.submits == 1, f"submits={a1.submits}")
    check("答对后调用了确认关闭", a1.confirmed)
    check("正确答案写回缓存", len(cache.stored) == 1 and cache.stored[0].source == "verified")

    # AI 答错，需要逐个试
    a2 = FakeAdapter(correct={"D"})
    ok2, _ = run_solve(a2, q, ["A"])
    check("答错后继续试直到答对", ok2, f"submits={a2.submits}")
    check("尝试次数不超过选项数", a2.submits <= 4, f"submits={a2.submits}")

    # 完全没有 AI 答案，纯穷举
    a3 = FakeAdapter(correct={"B"})
    ok3, _ = run_solve(a3, q, [])
    check("无 AI 答案也能试出来", ok3, f"submits={a3.submits}")

    # 多选
    qm = make_q(4, "multiple")
    a4 = FakeAdapter(correct={"A", "C"})
    ok4, _ = run_solve(a4, qm, ["A", "B"])
    check("多选能试到正确组合", ok4, f"submits={a4.submits}")

    # 永远判错 —— 必须能退出，不能死循环
    a5 = FakeAdapter(correct={"NOPE"}, max_rounds=40)
    ok5, _ = run_solve(a5, qm, ["A"])
    check("永远答错时会放弃而不是死循环", ok5 is False)
    from coursemate.answer.strategy import MAX_ATTEMPTS
    check("放弃前的尝试次数受上限约束", a5.submits <= MAX_ATTEMPTS, f"submits={a5.submits}")

    # 反馈不可判 —— 应立即停手，避免乱点
    a6 = FakeAdapter(correct={"A"}, feedback_override="unknown")
    ok6, _ = run_solve(a6, q, ["A"])
    check("无法判断对错时立即停手", ok6 is False and a6.submits == 1, f"submits={a6.submits}")

    # 保守模式：只填一次
    a7 = FakeAdapter(correct={"D"})
    ok7, _ = run_solve(a7, q, ["A"], retry=False)
    check("保守模式只提交一次", a7.submits == 1, f"submits={a7.submits}")

    # 弹窗中途自己消失（平台判定通过）
    a8 = FakeAdapter(correct={"Z"})
    a8.question_open = False
    ok8, _ = run_solve(a8, q, ["A"])
    check("弹窗已消失时视为通过", ok8 is True and a8.submits == 0)


def test_non_choice() -> None:
    print("\n== 非选择题 ==")
    from coursemate.answer.base import Question

    q = Question(stem="请简述", qtype="fill")
    a = FakeAdapter(correct=set())
    ok, _ = run_solve(a, q, [])
    check("填空题不自动作答", ok is False and a.submits == 0)


if __name__ == "__main__":
    print("CourseMate 试错答题测试")
    install_stub()
    for fn in (test_single_order, test_judge, test_multiple_order, test_no_options,
               test_retry_loop, test_non_choice):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
