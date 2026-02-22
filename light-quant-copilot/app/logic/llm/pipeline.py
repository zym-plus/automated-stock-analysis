"""
LLM 生成流水线（含质量校验 + 二次压缩 + 审计记录）。

完整流程：
  1. 高风险用语拦截（仅风控模块，检查最新用户消息）
     → _high_risk_response(phrase) → {draft:"", reply:说明, done:False, _keep_draft:True}
  2. 构建 system prompt + API messages
  3. 首次 LLM 调用
  4. 解析 JSON 响应
  5. 质量校验（规则层，不调用模型）
     5a. needs_compression → 调用 _compress()（best-effort）
     5b. ok==False → 加强约束重试一次
         重试通过 → 使用重试结果（再检查是否需压缩）
         重试失败/有改善 → 使用较好结果
         完全失败 → _degraded_result(issues)
  6. 写审计日志
  7. 返回 {draft, reply, done, _meta:{...}}

_keep_draft=True 时，上层 chat_ui.py 不覆盖当前 session 草稿。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .audit import AuditLogger
from .prompts import (
    PROMPT_VERSION,
    HIGH_RISK_REPLY_TEMPLATE,
    build_api_messages,
    build_compress_messages,
    build_high_risk_system_prompt,
    build_strict_retry_system_prompt,
    build_system_prompt,
    parse_llm_response,
)
from .validator import QualityValidator

if TYPE_CHECKING:
    from .router import LLMRouter

logger = logging.getLogger(__name__)

_COMPRESS_MAX_TOKENS: int = 1500

# ── 降级响应模板 ──────────────────────────────────────────────────

_FALLBACK_DRAFT_TPL = """\
## ⚠️ 生成质量未达标

本次 AI 生成未通过内容质量校验，问题如下：

{issues}

**您可以：**
1. 在右侧对话框补充更详细的数据（股票代码、持仓成本、具体交易记录等）
2. 直接在左侧草稿区手动编辑
3. 点击「← 返回修改输入」完善 Step1 数据后重新生成"""

_FALLBACK_REPLY_TPL = (
    "⚠️ 本次生成未通过内容质量校验（{n} 项问题），"
    "已显示降级提示。请补充数据后继续，或手动编辑草稿。"
)


# ══════════════════════════════════════════════════════════════════
# 主流水线
# ══════════════════════════════════════════════════════════════════

class LLMPipeline:
    """
    封装从 system prompt 构建到最终结果返回的全流程。

    每次 RealProvider.chat() 调用应实例化一个新的 LLMPipeline，
    避免跨次调用的状态污染。

    Args:
        router: 已初始化的 LLMRouter 实例（不得为 mock 模式）
    """

    def __init__(self, router: LLMRouter) -> None:
        self._router = router
        self._validator = QualityValidator()
        self._auditor = AuditLogger()

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def run(
        self,
        module: str,
        data: dict,
        messages: list[dict],
    ) -> dict:
        """
        执行完整生成流水线。

        Returns:
            {
              "draft":  str,
              "reply":  str,
              "done":   bool,
              "_keep_draft": bool,          # True → 上层不覆盖现有草稿
              "_meta":  {
                "provider": str,
                "model":    str,
                "retry":    bool,
                "compressed": bool,
                "high_risk_intercepted": bool,
              }
            }
        """
        n_user = sum(1 for m in messages if m.get("role") == "user")
        provider_name = self._router.active_provider
        model_name = self._router.active_model
        retry = False
        compressed = False

        # ── 1. 高风险用语拦截（风控模块，检查最新用户消息）──────────
        if module == "risk" and messages:
            last_user = next(
                (m.get("content", "") for m in reversed(messages)
                 if m.get("role") == "user"),
                "",
            )
            risk_check = self._validator.check_user_input_risk(last_user)
            if risk_check.high_risk_detected:
                result = self._high_risk_response(risk_check.high_risk_phrase)
                self._auditor.log(
                    module=module,
                    provider=provider_name,
                    model=model_name,
                    prompt_version=PROMPT_VERSION,
                    n_user_msgs=n_user,
                    validated=False,
                    retry=False,
                    compressed=False,
                    high_risk_intercepted=True,
                    issues=[f"高风险用语拦截：「{risk_check.high_risk_phrase}」"],
                )
                result["_meta"] = {
                    "provider": provider_name,
                    "model": model_name,
                    "retry": False,
                    "compressed": False,
                    "high_risk_intercepted": True,
                }
                return result

        # ── 2. 构建 prompt + messages ─────────────────────────────────
        system_prompt = build_system_prompt(module, data)
        api_msgs = build_api_messages(messages)

        # ── 3. 首次 LLM 调用 ──────────────────────────────────────────
        cfg_max_tokens = getattr(getattr(self._router, "_config", None), "max_tokens", 2048)
        cfg_temperature = getattr(getattr(self._router, "_config", None), "temperature", 0.7)
        raw = self._router.chat(
            api_msgs,
            system_prompt=system_prompt,
            json_mode=True,
            max_tokens=cfg_max_tokens,
            temperature=cfg_temperature,
        )

        if raw.get("error"):
            # Router 层 fallback 均失败
            return self._error_result(raw["error"])

        # ── 4. 解析 JSON ──────────────────────────────────────────────
        parsed = parse_llm_response(raw["text"], module, messages)
        draft = parsed["draft"]

        # ── 5. 质量校验 ───────────────────────────────────────────────
        v_result = self._validator.check_draft(draft, module, data)
        issues = list(v_result.issues)

        # ── 5a. 压缩（best-effort，不影响校验结果）───────────────────
        if v_result.needs_compression:
            compressed_draft = self._compress(draft)
            if compressed_draft:
                draft = compressed_draft
                parsed["draft"] = draft
                compressed = True

        # ── 5b. 校验失败 → 加强约束重试 ─────────────────────────────
        if not v_result.ok:
            retry = True
            logger.info(
                "质量校验失败（module=%s），触发加强重试。issues=%s",
                module, issues,
            )
            strict_prompt = build_strict_retry_system_prompt(module, data, issues)
            raw2 = self._router.chat(
                api_msgs,
                system_prompt=strict_prompt,
                json_mode=True,
                max_tokens=cfg_max_tokens,
                temperature=max(0.3, cfg_temperature - 0.2),  # 降低随机性
            )

            if not raw2.get("error"):
                parsed2 = parse_llm_response(raw2["text"], module, messages)
                v_result2 = self._validator.check_draft(parsed2["draft"], module, data)

                if v_result2.ok or len(v_result2.issues) < len(issues):
                    # 重试有改善，采用重试结果
                    parsed = parsed2
                    draft = parsed2["draft"]
                    issues = list(v_result2.issues)
                    # 再检查压缩
                    if v_result2.needs_compression and not compressed:
                        cd = self._compress(draft)
                        if cd:
                            draft = cd
                            parsed["draft"] = draft
                            compressed = True
                else:
                    # 重试后仍无改善 → 降级
                    logger.warning(
                        "重试后仍校验失败（module=%s），返回降级响应。issues=%s",
                        module, v_result2.issues,
                    )
                    issues = list(v_result2.issues)
                    parsed = self._degraded_result(issues)
            else:
                # 重试请求本身失败 → 降级
                logger.warning(
                    "重试 LLM 调用失败（module=%s），返回降级响应。error=%s",
                    module, raw2["error"],
                )
                parsed = self._degraded_result(issues)

        # ── 6. 审计日志 ───────────────────────────────────────────────
        self._auditor.log(
            module=module,
            provider=provider_name,
            model=model_name,
            prompt_version=PROMPT_VERSION,
            n_user_msgs=n_user,
            validated=v_result.ok,
            retry=retry,
            compressed=compressed,
            issues=issues,
        )

        return {
            "draft": parsed["draft"],
            "reply": parsed["reply"],
            "done": parsed["done"],
            "_keep_draft": False,
            "_meta": {
                "provider": provider_name,
                "model": model_name,
                "retry": retry,
                "compressed": compressed,
                "high_risk_intercepted": False,
            },
        }

    # ------------------------------------------------------------------
    # 内部：高风险拦截响应
    # ------------------------------------------------------------------

    def _high_risk_response(self, phrase: str) -> dict:
        """
        检测到高风险用语后：调用 LLM 解释风险（不生成执行方案）。
        LLM 调用失败时使用硬编码模板。

        返回 _keep_draft=True，告知上层不要覆盖当前草稿。
        """
        explain_msgs = [
            {
                "role": "user",
                "content": (
                    f"有人在股票交易中想要「{phrase}」，"
                    "请用 150 字以内解释这种操作的 3 个主要风险，"
                    "最后提 1 个帮助对方重新评估的问题。"
                    "不要给出任何具体操作价格或仓位建议，不要评判对方。"
                ),
            }
        ]
        raw = self._router.chat(
            explain_msgs,
            system_prompt=build_high_risk_system_prompt(phrase),
            json_mode=False,
            max_tokens=400,
            temperature=0.3,
        )

        if raw.get("error") or not raw.get("text", "").strip():
            reply = HIGH_RISK_REPLY_TEMPLATE.format(phrase=phrase)
        else:
            reply = (
                f"⚠️ 检测到高风险操作描述（「{phrase}」），"
                "已暂停生成执行方案，请先评估以下风险：\n\n"
                + raw["text"].strip()
            )

        return {
            "draft": "",
            "reply": reply,
            "done": False,
            "_keep_draft": True,
        }

    # ------------------------------------------------------------------
    # 内部：二次压缩
    # ------------------------------------------------------------------

    def _compress(self, draft: str) -> str:
        """
        调用 LLM 压缩 draft，返回压缩后文本。
        失败或结果过短（< 原始 30%）时返回空字符串（由调用方使用原稿）。
        """
        compress_msgs = build_compress_messages(draft)
        raw = self._router.chat(
            compress_msgs,
            system_prompt="",
            json_mode=False,
            max_tokens=_COMPRESS_MAX_TOKENS,
            temperature=0.3,
        )
        if raw.get("error"):
            logger.warning("压缩调用失败：%s", raw["error"])
            return ""
        compressed = raw.get("text", "").strip()
        # 防止过度截断：压缩结果至少为原始的 30%
        if not compressed or len(compressed) < len(draft) * 0.3:
            logger.warning(
                "压缩结果异常（原始 %d 字 → %d 字），丢弃压缩结果",
                len(draft), len(compressed),
            )
            return ""
        return compressed

    # ------------------------------------------------------------------
    # 内部：降级响应 & 错误响应
    # ------------------------------------------------------------------

    @staticmethod
    def _degraded_result(issues: list[str]) -> dict:
        issues_text = (
            "\n".join(f"- {i}" for i in issues)
            if issues
            else "- 内容质量未达标（具体原因未知）"
        )
        return {
            "draft": _FALLBACK_DRAFT_TPL.format(issues=issues_text),
            "reply": _FALLBACK_REPLY_TPL.format(n=len(issues)),
            "done": False,
        }

    @staticmethod
    def _error_result(error: str) -> dict:
        return {
            "draft": "",
            "reply": f"⚠️ LLM 调用失败：{error[:120]}",
            "done": False,
            "_keep_draft": True,
            "_meta": {
                "provider": "error",
                "model": "error",
                "retry": False,
                "compressed": False,
                "high_risk_intercepted": False,
            },
        }
