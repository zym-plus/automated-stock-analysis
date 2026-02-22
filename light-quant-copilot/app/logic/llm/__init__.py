"""
LLM 接入层 — 国产优先。

支持 Provider：DeepSeek / 阿里千问（Qwen）
占位 Provider：智谱 GLM / Moonshot Kimi / 火山方舟
无 API Key 时由上层 providers.py 自动回退 MockProvider。

公开接口：
  get_router(config=None)   → LLMRouter  全局单例，首次调用时初始化
  rebuild_router(config)    → LLMRouter  重置路由器（UI 修改设置后调用）
"""
from __future__ import annotations

from .base import LLMClient
from .config import LLMConfig, load_config
from .router import LLMRouter

__all__ = [
    "LLMClient",
    "LLMConfig",
    "load_config",
    "LLMRouter",
    "get_router",
    "rebuild_router",
]

_router: LLMRouter | None = None


def get_router(config: LLMConfig | None = None) -> LLMRouter:
    """返回全局 LLMRouter 单例；首次调用时用 config（或 env vars）初始化。"""
    global _router
    if _router is None:
        _router = LLMRouter(config or load_config())
    return _router


def rebuild_router(config: LLMConfig) -> LLMRouter:
    """用新配置重建路由器（UI 修改设置后调用）。"""
    global _router
    _router = LLMRouter(config)
    return _router
