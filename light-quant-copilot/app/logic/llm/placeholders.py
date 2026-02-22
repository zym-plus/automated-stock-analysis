"""
占位 Provider 类 — 预留接口，后续版本实现。

TODO 列表：
  ZhipuClient          智谱 GLM-4   https://open.bigmodel.cn
  KimiClient           Moonshot     https://platform.moonshot.cn
  VolcengineArkClient  火山方舟豆包  https://www.volcengine.com/product/ark

实现时参考 deepseek.py 或 qwen.py，均为 OpenAI 兼容接口。
"""
from __future__ import annotations

from .base import LLMClient


class ZhipuClient(LLMClient):
    """智谱 GLM — TODO: 实现。"""

    name = "zhipu"
    _TODO = "ZhipuClient 尚未实现，请等待后续版本或自行参考 deepseek.py 接入。"

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        pass

    def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> dict:
        return {
            "text": "",
            "provider": self.name,
            "model": "glm-4",
            "usage": None,
            "error": self._TODO,
        }


class KimiClient(LLMClient):
    """Moonshot Kimi — TODO: 实现。"""

    name = "kimi"
    _TODO = "KimiClient 尚未实现，请等待后续版本或自行参考 deepseek.py 接入。"

    def __init__(self, **kwargs) -> None:
        pass

    def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> dict:
        return {
            "text": "",
            "provider": self.name,
            "model": "moonshot-v1-8k",
            "usage": None,
            "error": self._TODO,
        }


class VolcengineArkClient(LLMClient):
    """火山方舟（豆包）— TODO: 实现。"""

    name = "volcengine"
    _TODO = "VolcengineArkClient 尚未实现，请等待后续版本或自行参考 deepseek.py 接入。"

    def __init__(self, **kwargs) -> None:
        pass

    def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> dict:
        return {
            "text": "",
            "provider": self.name,
            "model": "doubao-pro-4k",
            "usage": None,
            "error": self._TODO,
        }
