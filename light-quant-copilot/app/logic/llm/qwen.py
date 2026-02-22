"""
QwenClient — 阿里百炼 / 千问，使用 DashScope OpenAI 兼容接口。

兼容接口端点：https://dashscope.aliyuncs.com/compatible-mode/v1
文档：https://help.aliyun.com/zh/model-studio/developer-reference/use-qwen-by-calling-api

推荐模型：
  qwen-plus    均衡性价比（推荐，速度与质量兼顾）
  qwen-turbo   最快（适合简单任务）
  qwen-max     最强（耗时较长，适合复杂分析）
  qwen-long    支持超长上下文

依赖：pip install openai>=1.0.0
"""
from __future__ import annotations

import logging
import time

from .base import LLMClient

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    _OPENAI_OK = True
except ImportError:
    _OPENAI_OK = False


class QwenClient(LLMClient):
    name = "qwen"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout: int = 60,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = None

        if not _OPENAI_OK:
            logger.warning(
                "openai 包未安装；QwenClient 不可用。运行：pip install openai"
            )
            return
        if not api_key:
            logger.warning("QWEN_API_KEY 未设置；QwenClient 不可用。")
            return
        try:
            self._client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=float(timeout),
            )
        except Exception as exc:
            logger.error("QwenClient 初始化失败：%s", exc)

    # ── 核心接口 ─────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> dict:
        if self._client is None:
            return self._err(
                "openai 包未安装或 Qwen API Key 未设置，请在设置面板中配置"
            )

        msgs: list[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(
            {"role": m["role"], "content": m["content"]} for m in messages
        )

        kwargs: dict = dict(
            model=self._model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_error = ""
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                text = (resp.choices[0].message.content or "").strip()
                usage = None
                if resp.usage:
                    usage = {
                        "prompt_tokens": resp.usage.prompt_tokens,
                        "completion_tokens": resp.usage.completion_tokens,
                    }
                return {
                    "text": text,
                    "provider": self.name,
                    "model": self._model,
                    "usage": usage,
                    "error": None,
                }
            except Exception as exc:
                last_error = self._classify(exc)
                if "认证" in last_error:
                    break
                if attempt < self._max_retries:
                    time.sleep(2 ** attempt)

        return self._err(last_error)

    # ── 内部工具 ─────────────────────────────────────────────────

    @staticmethod
    def _classify(exc: Exception) -> str:
        msg = str(exc)
        if any(k in msg for k in ("401", "Unauthorized", "Invalid API-key", "authentication", "Apikey")):
            return "认证失败：Qwen API Key 无效，请检查设置"
        if any(k in msg for k in ("403", "Forbidden", "forbidden")):
            return "鉴权失败（403）：API Key 或网关权限校验失败，请检查 Key 与中转站配置"
        if any(k in msg for k in ("404", "Not Found", "not found")):
            return "路径错误（404）：Base URL 路径有误，常见原因是缺少 /v1，请检查中转站地址"
        if any(k in msg for k in ("502", "503", "504", "Bad Gateway", "Service Unavailable", "Gateway Timeout")):
            return "网关故障（5xx）：中转站或上游模型服务暂时不可用，请稍后重试"
        if any(k in msg for k in ("429", "rate limit", "Throttling", "Flow")):
            return "请求限流：请稍后重试"
        if any(k in msg.lower() for k in ("timeout", "timed out", "connect")):
            return "连接超时：请检查网络或稍后重试"
        logger.warning("Qwen API 调用失败（错误详情已脱敏）")
        return f"调用失败：{msg[:80]}"

    def _err(self, msg: str) -> dict:
        return {
            "text": "",
            "provider": self.name,
            "model": self._model,
            "usage": None,
            "error": msg,
        }
