"""
LLM 输出质量校验器（规则校验，不依赖模型）。

检查维度：
  1. 必要结构：模块要求的节标题是否齐全
  2. 违规表达：承诺收益 / 买卖指令 / 炒作用语
  3. 输出长度：超过阈值则标记需压缩
  4. 不确定性表述：数据不完整时必须出现"数据不足/不确定"等关键词
  5. 高风险用语检测：风控模块用户输入专用
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── 模块必要节标题 ──────────────────────────────────────────────────

_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "morning": [
        "今日一句话概况",
        "自选股关注清单",
        "今日 3 个风险",
        "今天你能做的 3 个动作",
    ],
    "risk": [
        "计划摘要",
        "需要确认的 6 个问题",
        "最容易亏钱的 3 点",
        "当前可做风控动作",
    ],
    "review": [
        "做得好的 2 点",
        "要改的 1 点",
        "执行 vs 认知",
        "明天 3 条风险提醒",
    ],
}

# ── 违规表达（荐股 / 承诺收益 / 炒作用语）──────────────────────────

_PROHIBITED_EXPRS: list[str] = [
    # 涨跌承诺
    "一定涨", "肯定涨", "必涨", "稳涨", "必然上涨",
    # 买卖指令
    "建议买入", "建议卖出", "推荐买入", "推荐卖出",
    "可以买", "可以卖", "应该买", "应该卖",
    # 炒作用语
    "稳赚", "包赚", "必赚", "无风险收益",
    "抄底机会", "绝佳买点", "最佳买点", "底部信号",
    # 保证承诺
    "保证收益", "承诺收益", "保底收益",
    # 绝对化
    "一定会涨", "肯定会涨", "涨定了",
]

# ── 不确定性关键词（数据不完整时必须出现其中之一）─────────────────

_UNCERTAINTY_KEYWORDS: list[str] = [
    "数据不足", "不确定", "无法判断", "待补充",
    "不可预测", "难以判断", "暂无数据", "缺少数据",
    "有待观察", "尚不明确", "未知",
]

# ── 高风险用语（风控模块，针对用户输入）─────────────────────────

_HIGH_RISK_PHRASES: list[str] = [
    "梭哈", "翻本", "赌一把", "all in", "allin",
    "满仓押", "全仓押", "孤注一掷", "背水一战",
    "最后一搏", "全押", "赌上", "赌这一把",
    "押注全部", "孤注",
]

# ── 长度阈值（中文字符数，超出触发压缩）─────────────────────────

_COMPRESS_THRESHOLD: int = 2000


# ══════════════════════════════════════════════════════════════════
# 结果结构
# ══════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """校验结果。ok=False 表示有规则违规需重试；needs_compression 独立标记。"""

    ok: bool = True
    issues: list[str] = field(default_factory=list)
    needs_compression: bool = False
    high_risk_detected: bool = False
    high_risk_phrase: str = ""

    def _fail(self, reason: str) -> None:
        """标记失败并记录原因（内部使用）。"""
        self.ok = False
        self.issues.append(reason)


# ══════════════════════════════════════════════════════════════════
# 校验器
# ══════════════════════════════════════════════════════════════════

class QualityValidator:
    """规则校验器；所有检查均为字符串匹配，不调用任何模型。"""

    # ------------------------------------------------------------------
    # 公开：草稿校验
    # ------------------------------------------------------------------

    def check_draft(
        self,
        draft: str,
        module: str,
        data: dict,
    ) -> ValidationResult:
        """
        检查 LLM 生成的 Markdown 草稿。

        Args:
            draft:  LLM 输出的草稿文本
            module: "morning" | "risk" | "review"
            data:   用户输入数据（判断数据完整性用）

        Returns:
            ValidationResult（ok=False 表示有违规需重试）
        """
        result = ValidationResult()

        # 1. 必要节标题
        for section in _REQUIRED_SECTIONS.get(module, []):
            if section not in draft:
                result._fail(f"缺少必要节标题：「{section}」")

        # 2. 违规表达
        for expr in _PROHIBITED_EXPRS:
            if expr in draft:
                result._fail(f"包含违规表达：「{expr}」")

        # 3. 长度检查（不算校验失败，但需压缩）
        if len(draft) > _COMPRESS_THRESHOLD:
            result.needs_compression = True

        # 4. 数据不完整场景下必须有不确定性表述
        if self._is_data_incomplete(module, data):
            has_uncertainty = any(kw in draft for kw in _UNCERTAINTY_KEYWORDS)
            if not has_uncertainty:
                result._fail("数据不完整但缺少「数据不足/不确定/无法判断」等表述")

        return result

    # ------------------------------------------------------------------
    # 公开：用户输入高风险检测（风控模块专用）
    # ------------------------------------------------------------------

    def check_user_input_risk(self, user_input: str) -> ValidationResult:
        """
        检查用户输入是否含高风险操作意图描述。
        仅用于风控模块的用户消息。
        """
        result = ValidationResult()
        # 去空格、小写，提高命中率
        text_normalized = user_input.lower().replace(" ", "").replace("　", "")
        for phrase in _HIGH_RISK_PHRASES:
            if phrase.lower() in text_normalized:
                result.high_risk_detected = True
                result.high_risk_phrase = phrase
                break
        return result

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _is_data_incomplete(module: str, data: dict) -> bool:
        """判断当前模块的核心数据是否不完整。"""
        if module == "morning":
            confirmed = data.get("_confirmed_table", [])
            stocks_raw = data.get("stocks", "").strip()
            return not confirmed and not stocks_raw
        if module == "risk":
            return not data.get("positions", [])
        if module == "review":
            return (
                not data.get("trades", [])
                and not data.get("notes", "").strip()
            )
        return False
