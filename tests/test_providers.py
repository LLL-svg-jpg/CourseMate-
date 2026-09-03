"""服务商定义与模型获取测试。

重点覆盖两件容易出错又不容易发现的事：
- 旧配置的 provider 值升级后还能不能读出来（读不出来就静默变成别家）
- 拉取模型失败时有没有给出人能看懂的原因（否则用户只看到"没反应"）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate import providers as P

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def test_registry() -> None:
    print("\n== 服务商注册表 ==")
    check("数量不少于 10 家", len(P.PROVIDERS) >= 10, str(len(P.PROVIDERS)))
    check("包含自定义选项", "custom" in P.PROVIDERS)
    check("显示名唯一", len(set(P.labels())) == len(P.labels()))
    check("显示名反查完整",
          all(P.LABEL_TO_KEY[p.label] == k for k, p in P.PROVIDERS.items()))
    for key, prov in P.PROVIDERS.items():
        check(f"{key} 的 key 与字典键一致", prov.key == key, f"{prov.key} vs {key}")


def test_legacy() -> None:
    print("\n== 旧配置兼容 ==")
    # 早期版本把自定义叫 openai_compatible，升级后不能读不出来
    check("openai_compatible 映射到 custom", P.get("openai_compatible").key == "custom")
    check("未知键落到自定义而非报错", P.get("某个不存在的").key == "custom")
    check("空串也不崩", P.get("").key == "custom")


def test_verified_models() -> None:
    print("\n== 模型列表 ==")
    ds = P.get("deepseek")
    check("DeepSeek 含 V4 系列", "deepseek-v4-flash" in ds.models, str(ds.models))
    # 旧名 deepseek-chat 仍保留作备选（部分中转站还在用），
    # 但默认必须是官方文档核对过的当前型号，否则用户开箱即错
    check("默认型号是核对过的 V4", ds.models[0] == "deepseek-v4-flash", ds.models[0])
    check("旧型号排在新型号之后",
          ds.models.index("deepseek-v4-flash") < ds.models.index("deepseek-chat"))
    check("不列代码/视觉专用模型", not any(
        any(t in m for t in ("vision", "coder", "codestral", "devstral"))
        for p in P.PROVIDERS.values() for m in p.models))

    # 每家的第一个模型就是界面默认选中项，不能是占位文字
    for key, prov in P.PROVIDERS.items():
        if prov.models:
            first = prov.models[0]
            check(f"{key} 默认型号不是占位文字",
                  not first.startswith("请"), first)
    check("每家都写了提示", all(p.note for p in P.PROVIDERS.values() if p.models))
    # 列表要精简：下拉框里塞十几个型号，用户根本挑不动
    for key, prov in P.PROVIDERS.items():
        if prov.models:
            check(f"{key} 模型数量克制（<=5）", len(prov.models) <= 5, str(len(prov.models)))


def test_protocol() -> None:
    print("\n== 协议与地址 ==")
    check("anthropic 走自有协议", P.get("anthropic").protocol == "anthropic")
    check("anthropic 不需要接口地址", not P.get("anthropic").needs_base_url)
    check("其余都需要接口地址",
          all(p.needs_base_url for k, p in P.PROVIDERS.items() if k != "anthropic"))
    check("非自定义的都预置了地址",
          all(p.base_url for k, p in P.PROVIDERS.items()
              if k not in ("anthropic", "custom")))


def test_fetch_errors() -> None:
    print("\n== 拉取模型的错误提示 ==")
    _, err = P.fetch_models("", "", "openai")
    check("缺 Key 时说清楚是缺 Key", "API Key" in err, err)

    _, err = P.fetch_models("", "sk-test", "openai")
    check("缺地址时说清楚是缺地址", "接口地址" in err, err)

    # 不可达地址：必须返回错误说明而不是抛异常
    models, err = P.fetch_models("http://127.0.0.1:9/v1", "sk-test", "openai", timeout=2)
    check("网络失败时不抛异常", isinstance(models, list) and bool(err))
    check("网络失败提示可读", "失败" in err or "网络" in err, err)


if __name__ == "__main__":
    print("CourseMate 服务商测试")
    for fn in (test_registry, test_legacy, test_verified_models,
               test_protocol, test_fetch_errors):
        fn()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
