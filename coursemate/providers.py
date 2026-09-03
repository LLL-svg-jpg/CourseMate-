"""AI 服务商定义与模型列表获取。

模型清单的三个来源（2026-09-02 核对）：
1. 官方文档实测（经浏览器 CDP 直连读取）：DeepSeek 定价页、智谱定价页。
2. GitHub 上 LiteLLM 维护的 model_prices_and_context_window.json，
   3518 条模型记录，覆盖各家真实 API 模型 ID 与上下文长度。
3. CC Switch 内置定价表（只取了模型名，没有碰其中任何密钥）。

每家只列答题真正用得上的型号：便宜的主力放第一个（即默认值），
外加一两个更强的备选。代码模型、视觉模型、旧版本一律不列——
答选择题用不到，列出来只会让下拉框变长、让人难挑。

即便如此，写死的列表仍然会过时。真正准确的来源是 fetch_models()：
拿用户自己的 Key 去问服务商此刻到底有哪些模型，界面上的
「获取模型列表」按钮走的就是这条路。

模型排序按「答题场景的性价比」：答选择题不需要顶配模型，
所以便宜够用的排前面，第一个即为默认选中项。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Provider:
    key: str
    label: str
    base_url: str
    models: list[str] = field(default_factory=list)
    note: str = ""
    protocol: str = "openai"      # openai / anthropic

    @property
    def needs_base_url(self) -> bool:
        return self.protocol != "anthropic"


PROVIDERS: dict[str, Provider] = {
    # ---------- 国内，答题首选 ----------
    "deepseek": Provider(
        key="deepseek",
        label="DeepSeek 深度求索",
        base_url="https://api.deepseek.com/v1",
        models=[
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-chat",
        ],
        note="v4-flash 便宜且上下文大，答题首选",
    ),
    "zhipu": Provider(
        key="zhipu",
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=[
            "glm-4.7-flash",
            "glm-5.3-flash",
            "glm-4.7",
            "glm-5.3",
        ],
        note="flash 系列便宜，答题够用",
    ),
    "moonshot": Provider(
        key="moonshot",
        label="月之暗面 Kimi",
        base_url="https://api.moonshot.cn/v1",
        models=[
            "kimi-latest",
            "kimi-k2.6",
            "kimi-k3",
        ],
        note="kimi-latest 会自动选型，省心",
    ),
    "qwen": Provider(
        key="qwen",
        label="通义千问 · 阿里云百炼",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=[
            "qwen-flash",
            "qwen-plus-latest",
            "qwen-max",
        ],
        note="qwen-flash 最便宜，答题够用",
    ),
    "minimax": Provider(
        key="minimax",
        label="MiniMax 稀宇",
        base_url="https://api.minimax.chat/v1",
        models=[
            "MiniMax-M2.5",
            "MiniMax-M3",
        ],
        note="模型名首字母大写，写成小写会调用失败",
    ),
    "doubao": Provider(
        key="doubao",
        label="豆包 · 火山方舟",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        models=[
            "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-pro-260215",
        ],
        note="部分账号要填「推理接入点 ID」，以火山控制台为准",
    ),
    "stepfun": Provider(
        key="stepfun",
        label="阶跃星辰 StepFun",
        base_url="https://api.stepfun.com/v1",
        models=[
            "step-3.5-flash",
        ],
        note="建议用「获取模型列表」确认",
    ),
    "siliconflow": Provider(
        key="siliconflow",
        label="硅基流动 SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        models=[
            "deepseek-ai/DeepSeek-V3",
            "zai-org/GLM-4.7",
        ],
        note="聚合平台模型极多且常变，务必用「获取模型列表」拉取",
    ),
    "baichuan": Provider(
        key="baichuan",
        label="百川 Baichuan",
        base_url="https://api.baichuan-ai.com/v1",
        models=[
            "Baichuan4",
            "Baichuan3-Turbo",
        ],
        note="建议用「获取模型列表」确认",
    ),

    # ---------- 海外，需要代理 ----------
    "anthropic": Provider(
        key="anthropic",
        label="Claude · Anthropic（海外）",
        base_url="",
        models=[
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5",
        ],
        note="国内直连需代理；答题用 haiku 就够，便宜很多",
        protocol="anthropic",
    ),
    "openai": Provider(
        key="openai",
        label="OpenAI（海外）",
        base_url="https://api.openai.com/v1",
        models=[
            "gpt-5.4-mini",
            "gpt-5.6",
            "o4-mini",
        ],
        note="国内直连需代理；mini 答题够用",
    ),
    "gemini": Provider(
        key="gemini",
        label="Google Gemini（海外）",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        models=[
            "gemini-3.7-flash",
            "gemini-3.1-pro-preview",
        ],
        note="国内直连需代理",
    ),
    "grok": Provider(
        key="grok",
        label="xAI Grok（海外）",
        base_url="https://api.x.ai/v1",
        models=[
            "grok-4-1-fast-non-reasoning",
            "grok-4.20",
        ],
        note="国内直连需代理",
    ),
    "mistral": Provider(
        key="mistral",
        label="Mistral（海外）",
        base_url="https://api.mistral.ai/v1",
        models=[
            "ministral-14b-latest",
            "magistral-small-latest",
        ],
        note="国内直连需代理",
    ),
    "cohere": Provider(
        key="cohere",
        label="Cohere（海外）",
        base_url="https://api.cohere.ai/compatibility/v1",
        models=[
            "command-r",
            "command-a-03-2025",
        ],
        note="国内直连需代理",
    ),

    # ---------- 兜底 ----------
    "custom": Provider(
        key="custom",
        label="自定义（任何 OpenAI 兼容接口）",
        base_url="",
        models=[],
        note="中转站、本地 Ollama、自建服务都填这里，地址和模型名自己填",
    ),
}

# 旧配置里的 provider 值 -> 新键，避免升级后配置读不出来
LEGACY_KEYS = {
    "openai_compatible": "custom",
    "google": "gemini",
    "xai": "grok",
    "volcengine": "doubao",
    "moonshot_cn": "moonshot",
}

LABEL_TO_KEY = {p.label: k for k, p in PROVIDERS.items()}


def get(key: str) -> Provider:
    """按键取服务商，未知的一律当自定义处理。"""
    key = LEGACY_KEYS.get(key, key)
    return PROVIDERS.get(key, PROVIDERS["custom"])


def labels() -> list[str]:
    return [p.label for p in PROVIDERS.values()]


def fetch_models(base_url: str, api_key: str, protocol: str = "openai",
                 timeout: float = 20.0,
                 proxy: str = "") -> tuple[list[str], str]:
    """向服务商索取当前可用的模型列表。

    返回 (模型列表, 错误说明)。成功时错误说明为空串。
    这是模型列表的权威来源——内置列表只是默认值，会过时。
    """
    try:
        import httpx
    except ImportError:
        return [], "缺少 httpx 依赖，无法联网获取。请先运行「安装依赖.bat」。"

    if not api_key:
        return [], "请先填写 API Key。"

    if protocol == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        if not base_url:
            return [], "请先填写接口地址。"
        url = base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}

    kwargs = {"headers": headers, "timeout": timeout}
    if proxy:
        kwargs["proxy"] = proxy
    try:
        resp = httpx.get(url, **kwargs)
    except Exception as exc:
        hint = "（海外服务商需要在设置里配好代理）" if not proxy else ""
        return [], f"网络请求失败：{type(exc).__name__}{hint}"

    if resp.status_code == 401:
        return [], "API Key 无效或已过期（401）。"
    if resp.status_code == 403:
        return [], "没有权限访问该接口（403），确认 Key 的权限范围。"
    if resp.status_code == 404:
        return [], "接口地址不对，该地址没有 /models 接口（404）。"
    if resp.status_code >= 400:
        return [], f"服务商返回错误 {resp.status_code}：{resp.text[:120]}"

    try:
        payload = resp.json()
    except Exception:
        return [], "服务商返回的不是合法 JSON，接口地址可能填错了。"

    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return [], "返回结构无法识别，没找到模型列表。"

    models = []
    for item in items:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("model") or item.get("name")
            if mid:
                models.append(str(mid))
        elif isinstance(item, str):
            models.append(item)

    if not models:
        return [], "服务商返回了空的模型列表。"
    return sorted(set(models)), ""
