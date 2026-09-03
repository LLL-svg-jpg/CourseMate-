"""平台适配器。

导入各平台模块以触发 @register 注册，resolve() 才能按 URL 找到它们。
新增平台时在这里加一行 import 即可。
"""

from . import chaoxing, zhihuishu  # noqa: F401  仅为触发注册
from .base import Lesson, PlatformAdapter, register, resolve, supported_platforms

__all__ = [
    "Lesson",
    "PlatformAdapter",
    "register",
    "resolve",
    "supported_platforms",
]
