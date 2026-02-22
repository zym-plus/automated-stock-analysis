"""
LLM 配置数据类（从环境变量读取，或由 UI 运行时传入覆盖）。

环境变量说明：
  LLM_PROVIDER      = mock | deepseek | qwen     （默认 mock）
  DEEPSEEK_API_KEY  = sk-xxx
  DEEPSEEK_MODEL    = deepseek-chat               （默认）
  DEEPSEEK_BASE_URL = https://api.deepseek.com    （默认）
  QWEN_API_KEY      = sk-xxx
  QWEN_MODEL        = qwen-plus                   （默认）
  QWEN_BASE_URL     = https://dashscope.aliyuncs.com/compatible-mode/v1
  GATEWAY_BASE_URL  = https://your-proxy.example.com/v1   （可选，中转站地址）
  LLM_TEMPERATURE   = 0.7
  LLM_MAX_TOKENS    = 2048
  LLM_TIMEOUT       = 60
  LLM_MAX_RETRIES   = 2
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LLMConfig:
    # ── Provider 选择 ────────────────────────────────────────────
    provider: str = "mock"          # "mock" | "deepseek" | "qwen"

    # ── DeepSeek ─────────────────────────────────────────────────
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # ── 阿里千问（DashScope OpenAI 兼容接口）─────────────────────
    qwen_api_key: str = ""
    qwen_model: str = "qwen-plus"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ── 中转站（网关）Base URL ────────────────────────────────────
    # 若设置，所有 OpenAI-compat 请求优先使用此地址（覆盖 provider 默认 base_url）
    gateway_base_url: str = ""

    # ── 通用参数 ─────────────────────────────────────────────────
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout: int = 60
    max_retries: int = 2


# ══════════════════════════════════════════════════════════════════
# Base URL 规范化
# ══════════════════════════════════════════════════════════════════

def normalize_base_url(url: str) -> str:
    """
    规范化 Base URL：
    - 去首尾空白 / 尾部斜杠
    - 验证 http(s):// 前缀；不合规则返回空串（表示"不使用网关"）
    - 若路径不以 /v1 结尾，则自动追加 /v1
    """
    from urllib.parse import urlparse
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        return ""
    path = urlparse(url).path.rstrip("/")
    if not (path == "/v1" or path.endswith("/v1")):
        url = url + "/v1"
    return url


def load_config() -> LLMConfig:
    """从环境变量加载配置（不存在的 Key 使用默认值）。"""
    return LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "mock").lower().strip(),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_base_url=os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ),
        qwen_api_key=os.getenv("QWEN_API_KEY", ""),
        qwen_model=os.getenv("QWEN_MODEL", "qwen-plus"),
        qwen_base_url=os.getenv(
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        gateway_base_url=os.getenv("GATEWAY_BASE_URL", ""),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
        timeout=int(os.getenv("LLM_TIMEOUT", "60")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )
