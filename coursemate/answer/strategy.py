"""答题尝试策略：生成"依次试到对为止"的候选序列。

为什么需要试错而不是只答一次：
AI 正确率再高也不是 100%，而课程平台的弹题答错了会重新弹出——
只答一次就关窗，答错的题会反复弹，视频永远播不下去，程序表现为"卡住"。
逐个试到平台判定正确，才能真正把播放推进下去。

排序原则是"最可能对的排前面"，这样绝大多数题第一次就命中，
不会真的把所有组合都点一遍：
- AI 给的答案永远排第一
- 单选：其余选项按原顺序补齐
- 多选：先试与 AI 答案只差一个选项的组合，再按选中个数展开
"""
from __future__ import annotations

from itertools import combinations

from .base import Question

# 多选组合数是 2^n-1，n=6 就有 63 种。真点满会被平台判定为异常行为，
# 所以设上限；超出部分不试，宁可放弃这道题也不能没完没了地点。
MAX_ATTEMPTS = 20


def _dedupe(seq: list[list[str]]) -> list[list[str]]:
    """按内容去重且保持顺序。选项集合相同视为同一次尝试。"""
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []
    for item in seq:
        key = tuple(sorted(item))
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_attempts(question: Question, ai_keys: list[str] | None = None) -> list[list[str]]:
    """生成按可能性排序的尝试序列。

    返回的每一项是一次尝试要选中的选项字母列表。
    """
    keys = [opt.key.upper() for opt in question.options]
    if not keys:
        return []

    ai = [k.upper() for k in (ai_keys or []) if k.upper() in keys]
    attempts: list[list[str]] = []

    if question.qtype == "multiple":
        if ai:
            attempts.append(list(ai))
            # 与 AI 答案只差一个的组合最可能是对的：AI 多选了一个或漏选了一个
            for k in ai:
                shrunk = [x for x in ai if x != k]
                if shrunk:
                    attempts.append(shrunk)
            for k in keys:
                if k not in ai:
                    attempts.append(sorted(ai + [k], key=keys.index))
        # 剩余组合按选中个数展开。多选题很少只选一个，所以从 2 个起
        for size in range(2, len(keys) + 1):
            for combo in combinations(keys, size):
                attempts.append(list(combo))
        for k in keys:
            attempts.append([k])
    else:
        # 单选与判断题：AI 答案优先，其余按页面顺序补齐
        if ai:
            attempts.append([ai[0]])
        attempts.extend([k] for k in keys)

    return _dedupe(attempts)[:MAX_ATTEMPTS]


def describe_attempt(attempt: list[str], index: int, total: int) -> str:
    return f"第 {index}/{total} 次尝试：选 {'+'.join(attempt)}"
