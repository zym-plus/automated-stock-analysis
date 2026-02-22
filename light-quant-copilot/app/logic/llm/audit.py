"""
可审计日志记录器。

记录内容（JSON-lines 格式）：
  - 时间戳（UTC ISO 8601）
  - 模块、provider 名称、model 名称
  - prompt 版本号
  - 用户消息轮数
  - 是否触发质量校验重试
  - 是否触发压缩
  - 是否触发高风险拦截
  - 校验问题列表

安全约束：绝不记录任何 API Key 或用户个人信息。
日志路径：~/.light-quant-copilot/audit.jsonl（可用 LQC_LOG_DIR 覆盖目录）
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = Path.home() / ".light-quant-copilot"


def _default_log_path() -> Path:
    log_dir = Path(os.environ.get("LQC_LOG_DIR", str(_DEFAULT_LOG_DIR)))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("无法创建审计日志目录 %s：%s", log_dir, e)
        log_dir = Path.cwd()
    return log_dir / "audit.jsonl"


class AuditLogger:
    """
    JSON-lines 格式审计日志。
    每次 LLM 调用（包含高风险拦截）写入一条记录。
    线程安全：文件以追加模式打开，每条记录原子写入一行。
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = log_path or _default_log_path()

    def log(
        self,
        *,
        module: str,
        provider: str,
        model: str,
        prompt_version: str,
        n_user_msgs: int,
        validated: bool,
        retry: bool,
        compressed: bool,
        high_risk_intercepted: bool = False,
        issues: list[str] | None = None,
    ) -> None:
        """
        写入一条审计记录。

        所有参数必须以关键字方式传递。
        失败时仅记录 warning 日志，不抛出异常（不影响主流程）。
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "module": module,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "n_user_msgs": n_user_msgs,
            "validated": validated,
            "retry": retry,
            "compressed": compressed,
            "high_risk_intercepted": high_risk_intercepted,
            "issues": issues or [],
        }
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("审计日志写入失败（路径：%s）：%s", self._log_path, e)
