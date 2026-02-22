"""
LLMRouter — Provider 路由与自动回退。

路由策略：
  1. 使用配置指定的 provider（若有 Key 且初始化成功）
  2. 若主 provider 调用失败，尝试其他已配置的 provider
  3. 所有真实 provider 均失败时返回 error，由上层 RealProvider 回退 MockProvider

is_mock=True 表示无任何有效 Key，上层应直接使用 MockProvider。
"""
from __future__ import annotations

import logging

from .base import LLMClient
from .config import LLMConfig

logger = logging.getLogger(__name__)


class LLMRouter:
    """管理多个 LLMClient，实现自动路由与 fallback。"""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._clients: dict[str, LLMClient] = {}
        self._resolved_bases: dict[str, str] = {}
        self._active_name: str = "mock"
        self._build(config)

    # ── 初始化 ───────────────────────────────────────────────────

    @staticmethod
    def _resolved_base(cfg: LLMConfig, provider_default: str) -> str:
        """优先使用规范化后的网关 URL；若网关未配置或无效则退回 provider 默认。"""
        from .config import normalize_base_url
        if cfg.gateway_base_url:
            normalized = normalize_base_url(cfg.gateway_base_url)
            if normalized:
                return normalized
        return provider_default

    def _build(self, cfg: LLMConfig) -> None:
        """根据配置构建可用的 provider 客户端。"""
        if cfg.deepseek_api_key:
            try:
                from .deepseek import DeepSeekClient
                resolved = self._resolved_base(cfg, cfg.deepseek_base_url)
                self._resolved_bases["deepseek"] = resolved
                self._clients["deepseek"] = DeepSeekClient(
                    api_key=cfg.deepseek_api_key,
                    model=cfg.deepseek_model,
                    base_url=resolved,
                    timeout=cfg.timeout,
                    max_retries=cfg.max_retries,
                )
            except Exception as exc:
                logger.warning("DeepSeekClient 构建失败：%s", exc)

        if cfg.qwen_api_key:
            try:
                from .qwen import QwenClient
                resolved = self._resolved_base(cfg, cfg.qwen_base_url)
                self._resolved_bases["qwen"] = resolved
                self._clients["qwen"] = QwenClient(
                    api_key=cfg.qwen_api_key,
                    model=cfg.qwen_model,
                    base_url=resolved,
                    timeout=cfg.timeout,
                    max_retries=cfg.max_retries,
                )
            except Exception as exc:
                logger.warning("QwenClient 构建失败：%s", exc)

        # 确定激活 provider
        if cfg.provider in self._clients:
            self._active_name = cfg.provider
        elif self._clients:
            # 配置的 provider 无可用 Key，但其他有 Key，自动选择
            self._active_name = next(iter(self._clients))
            logger.info(
                "Provider '%s' 无可用 Key，自动切换到 '%s'",
                cfg.provider,
                self._active_name,
            )
        else:
            self._active_name = "mock"

    # ── 属性 ─────────────────────────────────────────────────────

    @property
    def active_provider(self) -> str:
        return self._active_name

    @property
    def is_mock(self) -> bool:
        return self._active_name == "mock"

    @property
    def active_model(self) -> str:
        """当前激活 provider 的模型名。"""
        if self._active_name == "mock":
            return "mock"
        client = self._clients.get(self._active_name)
        return getattr(client, "_model", "unknown") if client else "unknown"

    @property
    def active_base_url(self) -> str:
        """当前激活 provider 实际使用的 base_url（含网关地址）。"""
        return self._resolved_bases.get(self._active_name, "")

    # ── 核心调用 ─────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> dict:
        """
        调用激活 provider；失败时尝试 fallback 到其他已配置 provider。
        返回格式同 LLMClient.chat()。
        """
        if self.is_mock:
            return {
                "text": "",
                "provider": "mock",
                "model": "mock",
                "usage": None,
                "error": "mock_mode",
            }

        primary = self._clients.get(self._active_name)
        if primary is None:
            return {
                "text": "",
                "provider": "mock",
                "model": "mock",
                "usage": None,
                "error": "mock_mode",
            }

        result = primary.chat(messages, system_prompt, temperature, max_tokens, json_mode)

        if result.get("error") and result["error"] != "mock_mode":
            # 尝试 fallback 到其他已配置 provider
            for name, client in self._clients.items():
                if name == self._active_name:
                    continue
                fb = client.chat(
                    messages, system_prompt, temperature, max_tokens, json_mode
                )
                if not fb.get("error"):
                    fb["_fallback_from"] = self._active_name
                    logger.info(
                        "从 %s 回退到 %s 成功", self._active_name, name
                    )
                    return fb
            # 所有 provider 均失败，返回原始错误（由上层回退 Mock）
            return result

        return result

    # ── 测试 ─────────────────────────────────────────────────────

    def test_active(self) -> dict:
        """测试当前激活 provider 的连通性。"""
        if self.is_mock:
            return {
                "ok": True,
                "latency_ms": 0,
                "text": "演示模式（无需测试）",
                "error": "",
            }
        client = self._clients.get(self._active_name)
        if client is None:
            return {
                "ok": False,
                "latency_ms": 0,
                "text": "",
                "error": "provider 未初始化",
            }
        return client.test_connection()
