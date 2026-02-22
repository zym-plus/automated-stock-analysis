"""
本地 AI 配置持久化层。

存储位置：<project_root>/data/settings.json
优先级（高→低）：会话 UI 设置 > settings.json > .env 文件 > 默认值

安全约束：
  - mask_key() 脱敏显示，UI 不回显明文
  - save_settings() 只写白名单字段
  - 日志脱敏，不打印 Key
  - 文件存本机，不上传
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 存储路径（与 copilot.db 同目录）──────────────────────────────

_SETTINGS_PATH = Path(__file__).parent.parent.parent / "data" / "settings.json"

# ── 默认值（与 LLMConfig 字段对应）──────────────────────────────

_DEFAULTS: dict = {
    "provider": "mock",
    # DeepSeek
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-chat",
    "deepseek_base_url": "https://api.deepseek.com",
    # 阿里百炼/千问
    "qwen_api_key": "",
    "qwen_model": "qwen-plus",
    "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    # 中转站/网关（可选，覆盖所有 provider 的 base_url）
    "gateway_base_url": "",
    # 通用参数
    "temperature": 0.3,
    "max_tokens": 2048,
    "timeout": 60,
    "max_retries": 2,
}

# ── LLMConfig 支持的字段白名单（防止注入多余 key）───────────────

_ALLOWLIST = set(_DEFAULTS.keys())


# ══════════════════════════════════════════════════════════════════
# 读写
# ══════════════════════════════════════════════════════════════════

def load_settings() -> dict:
    """
    从本地文件加载配置；文件不存在或解析失败时返回默认值副本。
    返回值始终包含所有已知字段。
    """
    result = dict(_DEFAULTS)
    if not _SETTINGS_PATH.exists():
        return result
    try:
        with _SETTINGS_PATH.open("r", encoding="utf-8") as f:
            saved: dict = json.load(f)
        # 只合并白名单字段
        for k in _ALLOWLIST:
            if k in saved:
                result[k] = saved[k]
    except Exception as e:
        logger.warning("读取本地配置失败（%s）：%s", _SETTINGS_PATH, e)
    return result


def save_settings(settings: dict) -> bool:
    """
    将配置保存到本地文件（仅写入白名单字段）。

    Returns:
        True 表示成功，False 表示写入失败（日志中有详情）。
    """
    to_save = {k: settings.get(k, _DEFAULTS[k]) for k in _ALLOWLIST}
    # 不保存 Key 以外的字段到日志，防止泄露
    _log_safe = {k: ("****" if "api_key" in k and v else v)
                 for k, v in to_save.items()}
    logger.debug("保存设置（脱敏）：%s", _log_safe)
    try:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _SETTINGS_PATH.open("w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        logger.warning("保存本地配置失败（%s）：%s", _SETTINGS_PATH, e)
        return False


def has_saved_config() -> bool:
    """返回 True 表示 settings.json 存在（用户曾保存过配置）。"""
    return _SETTINGS_PATH.exists()


def settings_path() -> Path:
    """返回配置文件路径（供 UI 显示用）。"""
    return _SETTINGS_PATH


# ══════════════════════════════════════════════════════════════════
# Key 脱敏
# ══════════════════════════════════════════════════════════════════

def mask_key(key: str) -> str:
    """将 API Key 脱敏：保留前4位 + **** + 后4位。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


# ══════════════════════════════════════════════════════════════════
# 错误翻译
# ══════════════════════════════════════════════════════════════════

def translate_error(error: str) -> str:
    """
    将英文/混合错误信息转换为面向普通用户的中文友好提示。
    不含堆栈信息。
    """
    e = (error or "").lower()

    if any(k in e for k in [
        "401", "unauthorized", "invalid key", "invalid api key",
        "authentication", "认证失败", "api key 无效", "key 无效",
    ]):
        return "API Key 无效，请检查后重新填写（可能已过期或复制不完整）"

    if any(k in e for k in [
        "429", "rate limit", "too many requests", "限流",
        "quota exceeded", "请求过频",
    ]):
        return "调用频率超限，请稍等 10–30 秒后重试"

    if any(k in e for k in [
        "timeout", "timed out", "超时", "read timeout",
    ]):
        return "网络请求超时，请检查网络连接后重试"

    if any(k in e for k in [
        "insufficient", "balance", "余额不足", "欠费",
        "quota", "账户余额", "credits",
    ]):
        return "账户余额不足，请登录服务商平台充值后重试"

    if any(k in e for k in [
        "404", "路径错误", "not found",
    ]):
        return "Base URL 路径错误（404）：中转站地址路径有误，请检查是否包含 /v1"

    if any(k in e for k in [
        "403", "forbidden", "鉴权失败",
    ]):
        return "鉴权失败（403）：API Key 或网关配置有误，请确认 Key 对应该中转站"

    if any(k in e for k in [
        "502", "503", "504", "bad gateway", "service unavailable",
        "gateway timeout", "网关故障",
    ]):
        return "网关故障（5xx）：中转站或上游模型服务暂时不可用，请稍后重试或切换直连"

    if any(k in e for k in [
        "connection", "network", "unreachable", "无法连接",
        "name or service not known", "connection refused", "no route",
    ]):
        return "无法连接到服务器，请检查网络或稍后重试"

    if any(k in e for k in [
        "model", "not found", "does not exist", "模型不存在",
        "invalid model",
    ]):
        return "模型名称有误，请检查「高级选项」中的模型名称"

    if any(k in e for k in ["mock_mode", "演示模式"]):
        return "当前为演示模式，无实际 API 调用"

    # 通用兜底：截取前60字
    short = error.strip()[:60]
    return f"服务暂时不可用（{short}）"


# ══════════════════════════════════════════════════════════════════
# 与 LLMConfig 互转
# ══════════════════════════════════════════════════════════════════

def settings_to_llm_config(settings: dict):
    """将 settings dict 转换为 LLMConfig 实例。"""
    from app.logic.llm.config import LLMConfig
    return LLMConfig(
        provider=settings.get("provider", "mock"),
        deepseek_api_key=settings.get("deepseek_api_key", ""),
        deepseek_model=settings.get("deepseek_model", "deepseek-chat"),
        deepseek_base_url=settings.get("deepseek_base_url",
                                       "https://api.deepseek.com"),
        qwen_api_key=settings.get("qwen_api_key", ""),
        qwen_model=settings.get("qwen_model", "qwen-plus"),
        qwen_base_url=settings.get("qwen_base_url",
                                   "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        gateway_base_url=settings.get("gateway_base_url", ""),
        temperature=float(settings.get("temperature", 0.3)),
        max_tokens=int(settings.get("max_tokens", 2048)),
        timeout=int(settings.get("timeout", 60)),
        max_retries=int(settings.get("max_retries", 2)),
    )
