"""核心逻辑测试。

只覆盖不需要浏览器的部分：题目指纹、答案缓存、选项匹配、计时补偿、配置解析。
这些是出了错最难在真实刷课过程中察觉的地方——
选项匹配错一位，程序照样跑完，只是答案全错。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate.answer.ai import match_option
from coursemate.answer.base import AnswerResult, Option, Question, normalize
from coursemate.answer.cache import AnswerCache
from coursemate.config import Config, ConfigError
from coursemate.events import Interruption, StudyClock

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def q(stem: str, opts: list[str], qtype: str = "single") -> Question:
    return Question(
        stem=stem,
        options=[Option(key=chr(ord("A") + i), text=t) for i, t in enumerate(opts)],
        qtype=qtype,
    )


def test_normalize() -> None:
    print("\n== normalize ==")
    check("全角括号归一", normalize("（甲）") == normalize("(甲)"))
    check("空白折叠", normalize("a   b\n c") == "a b c")
    check("空串安全", normalize("") == "")


def test_fingerprint() -> None:
    print("\n== 题目指纹 ==")
    a = q("以下哪个是编程语言？", ["Python", "香蕉", "汽车"])
    b = q("以下哪个是编程语言？ ", ["Python", "香蕉", "汽车"])
    check("空白差异不影响指纹", a.fingerprint == b.fingerprint)

    c = q("1. 以下哪个是编程语言？", ["Python", "香蕉", "汽车"])
    check("题号前缀不影响指纹", a.fingerprint == c.fingerprint, f"{a.fingerprint} vs {c.fingerprint}")

    d = q("以下哪个是编程语言？", ["Python", "香蕉", "飞机"])
    check("选项不同则指纹不同", a.fingerprint != d.fingerprint)


def test_cache() -> None:
    print("\n== 答案缓存 ==")
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "a.db"
        cache = AnswerCache(db, enabled=True)
        question = q("首都是哪里？", ["北京", "上海"])

        check("未命中返回 None", cache.get(question) is None)

        cache.put(question, AnswerResult(option_keys=["A"], option_texts=["北京"],
                                         confidence=0.9, source="ai"))
        got = cache.get(question)
        check("写入后可命中", got is not None and got.option_keys == ["A"])
        check("命中标记来源为 cache", got is not None and got.source == "cache")

        cache.put(question, AnswerResult())  # 空答案
        got2 = cache.get(question)
        check("空答案不覆盖已有答案", got2 is not None and got2.option_keys == ["A"])

        total, hits = cache.stats()
        check("统计题目数正确", total == 1, f"total={total}")
        check("统计命中数累加", hits >= 2, f"hits={hits}")
        cache.close()

        disabled = AnswerCache(db, enabled=False)
        check("禁用时 get 返回 None", disabled.get(question) is None)
        disabled.put(question, AnswerResult(option_keys=["B"]))
        check("禁用时 put 不报错", True)


def test_match_option() -> None:
    print("\n== 选项匹配 ==")
    question = q("首都是哪里？", ["北京", "上海", "广州"])

    r1 = AnswerResult(option_keys=["A"], option_texts=["北京"])
    check("字母直接命中", match_option(r1, question) == ["A"])

    # 模型给了越界字母，但文本对得上 —— 应该靠文本救回来
    r2 = AnswerResult(option_keys=["D"], option_texts=["上海"])
    check("字母越界时按文本回退", match_option(r2, question) == ["B"],
          f"got {match_option(r2, question)}")

    r3 = AnswerResult(option_keys=["A", "C"], option_texts=["北京", "广州"])
    check("多选保留顺序", match_option(r3, question) == ["A", "C"])

    r4 = AnswerResult(option_keys=[], option_texts=["不存在的选项"])
    check("完全匹配不上返回空", match_option(r4, question) == [])

    r5 = AnswerResult(option_keys=["b"], option_texts=[])
    check("小写字母也能命中", match_option(r5, question) == ["B"])

    # 文本带空白差异
    r6 = AnswerResult(option_keys=["Z"], option_texts=[" 广州 "])
    check("文本含空白仍可匹配", match_option(r6, question) == ["C"])


def test_clock() -> None:
    print("\n== 学习计时 ==")
    clock = StudyClock()
    check("初始未达限时", not clock.reached(10))
    check("limit=0 视为不限时", not clock.reached(0))

    clock._start -= 600  # 伪造已过 10 分钟
    check("超过限时应触发", clock.reached(5))
    clock.add_paused(600)
    check("暂停时间从有效时长扣除", not clock.reached(5),
          f"elapsed={clock.elapsed_minutes:.2f}")
    check("暂停时长可读", abs(clock.paused_minutes - 10) < 0.1)

    clock.add_paused(-100)
    check("负数暂停被忽略", abs(clock.paused_minutes - 10) < 0.1)

    clock.reset()
    check("reset 清零", clock.paused_minutes == 0 and clock.elapsed_minutes < 0.1)


def test_interruption() -> None:
    print("\n== 中断信号 ==")

    async def scenario() -> float:
        it = Interruption("test")
        waiter = asyncio.create_task(it.wait())
        await asyncio.sleep(0.15)
        it.resolve()
        return await waiter

    waited = asyncio.run(scenario())
    check("wait 返回实际阻塞时长", 0.1 < waited < 1.0, f"waited={waited:.3f}")


def test_config() -> None:
    print("\n== 配置解析 ==")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "c.toml"
        p.write_text(
            '[course]\nurls = ["https://x.zhihuishu.com/a", "bad-url"]\n'
            "speed = 9.0\nlimit_max_minutes = 30\n"
            '[answer]\nenabled = true\napi_key = "k"\nprovider = "anthropic"\n',
            encoding="utf-8",
        )
        cfg = Config(p)
        check("非法 URL 被过滤", cfg.course_urls == ["https://x.zhihuishu.com/a"])
        check("倍速被夹到上限 2.0", cfg.speed == 2.0, f"speed={cfg.speed}")
        check("限时读取正确", cfg.limit_max_minutes == 30)
        check("auto_submit 默认 false", cfg.auto_submit is False)
        # 缺失 [browser] 段时 channel 默认 auto，会解析成本机实际装了的浏览器
        check("缺失段落用默认值", cfg.channel_raw == "auto", cfg.channel_raw)
        check("auto 解析为具体浏览器",
              cfg.channel in ("chrome", "msedge", "chromium"), cfg.channel)
        check("retry_until_correct 默认开启", cfg.retry_until_correct is True)

        # 热重载：改文件后无需重建对象
        p.write_text(
            '[course]\nurls = ["https://x.zhihuishu.com/a"]\nspeed = 1.25\n',
            encoding="utf-8",
        )
        check("倍速热重载生效", cfg.speed == 1.25, f"speed={cfg.speed}")

    try:
        Config(Path(tempfile.gettempdir()) / "definitely-missing-9x8.toml")
        check("缺失配置文件应报错", False)
    except ConfigError:
        check("缺失配置文件应报错", True)


def test_registry() -> None:
    print("\n== 平台注册 ==")
    try:
        from coursemate.platforms import resolve, supported_platforms
    except ImportError as exc:
        print(f"  [SKIP] 需要 playwright: {exc}")
        return
    check("智慧树 URL 可解析", resolve("https://studyh5.zhihuishu.com/x") is not None)
    check("超星 URL 可解析", resolve("https://mooc1.chaoxing.com/mycourse/x") is not None)
    check("无关 URL 返回 None", resolve("https://example.com") is None)
    check("平台列表非空", len(supported_platforms()) >= 2)


def test_browser_detect() -> None:
    """浏览器探测与路径宽容解析。

    这块出错的后果很直接：用户一点「开始刷课」就失败，
    而报错通常只说"浏览器启动失败"，看不出是没装 Chrome。
    """
    print("\n== 浏览器探测 ==")
    from coursemate.config import (
        detect_browser, find_installed_browser, resolve_browser_path,
    )

    channel, path = detect_browser()
    check("探测返回有效 channel", channel in ("chrome", "msedge", "chromium"), channel)
    check("未安装的 channel 返回 None", find_installed_browser("firefox") is None)

    if path:
        exe = Path(path)
        check("探测到的路径真实存在", exe.is_file(), str(exe))
        check("完整 exe 路径原样返回", resolve_browser_path(str(exe), channel) == str(exe))
        check("填安装目录也能找到 exe",
              resolve_browser_path(str(exe.parent), channel) == str(exe),
              resolve_browser_path(str(exe.parent), channel) or "None")
        check("填上一级目录同样能找到",
              resolve_browser_path(str(exe.parent.parent), channel) == str(exe),
              resolve_browser_path(str(exe.parent.parent), channel) or "None")

    check("空路径返回 None", resolve_browser_path("", "chrome") is None)
    missing = "D:" + chr(92) + "nope" + chr(92) + "x.exe"
    check("不存在的路径原样交回（让底层给出明确报错）",
          resolve_browser_path(missing, "chrome") == missing)


if __name__ == "__main__":
    print("CourseMate 核心逻辑测试")
    for fn in (
        test_normalize, test_fingerprint, test_cache, test_match_option,
        test_clock, test_interruption, test_config, test_registry, test_browser_detect,
    ):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
