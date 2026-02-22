"""
LLM 客户端抽象基类。

所有 Provider 实现必须继承 LLMClient 并实现 chat()。
错误时填充 "error" 字段而不是抛出异常，保证上层路由器可以安全判断并切换 fallback。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Iterator


class LLMClient(ABC):
    """统一的 LLM 调用接口。"""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> dict:
        """
        发起一次对话请求。

        Args:
            messages:      对话历史 [{"role": "user"|"assistant", "content": str}, ...]
            system_prompt: 系统提示词（可选）
            temperature:   生成温度（0–1）
            max_tokens:    最大输出 token 数
            json_mode:     True 时要求模型返回合法 JSON

        Returns:
            {
              "text":     str,          # 模型回复文本
              "provider": str,          # provider 名称
              "model":    str,          # 实际使用的模型名
              "usage":    dict | None,  # {"prompt_tokens": int, "completion_tokens": int}
              "error":    str | None,   # 非 None 时表示失败
            }
        """
        ...

    def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Iterator[str]:
        """
        流式对话（逐段输出 token）。
        默认实现退化为普通 chat()，子类可覆盖以实现真正流式输出。
        """
        result = self.chat(messages, system_prompt, temperature, max_tokens)
        if not result.get("error"):
            yield result["text"]

    @property
    def supports_vision(self) -> bool:
        """是否支持图片输入（Vision API）。"""
        return False

    def analyze_image(self, prompt: str, image_bytes: bytes) -> str:
        """分析图片，返回文本描述（不支持 Vision 的实现返回空字符串）。"""
        return ""

    def test_connection(self) -> dict:
        """
        快速连通性测试，发送一句话并检查是否有回复。

        Returns:
            {"ok": bool, "latency_ms": int, "text": str, "error": str}
        """
        t0 = time.time()
        result = self.chat(
            messages=[{"role": "user", "content": "你好，请只回复「连接成功」四个字。"}],
            max_tokens=20,
        )
        latency = int((time.time() - t0) * 1000)
        return {
            "ok": not bool(result.get("error")),
            "latency_ms": latency,
            "text": result.get("text", ""),
            "error": result.get("error") or "",
        }
