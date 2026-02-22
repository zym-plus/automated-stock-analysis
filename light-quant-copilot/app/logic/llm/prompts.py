"""
业务层 Prompt 构建器 & LLM 响应解析器。

职责：
  1. 将 module + 丰富的 data（含 Step2 confirmed_table）格式化为 system_prompt
  2. 将已有对话 messages 转换成 OpenAI 兼容格式
  3. 解析 LLM JSON 响应为 {draft, reply, done}，并强制最少轮次约束
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Prompt 版本号（写入审计日志，便于追溯）
PROMPT_VERSION = "v2.0"


# ══════════════════════════════════════════════════════════════════
# 专业性约束（凌驾于所有模块约束之上，统一注入 build_system_prompt）
# ══════════════════════════════════════════════════════════════════

_PROFESSIONAL_GUARDRAILS = """\
【专业性约束 — 优先级最高，凌驾于本次指令中的所有其他要求】

■ 角色定位
  你是理性审慎的量化风险分析助手，偏向研究与风险管理。
  你不是荐股顾问，不对任何投资结果负责，不提供任何收益承诺。

■ 绝对禁止（以下任意表达出现均构成严重违规）
  · 涨跌承诺：「一定涨」「肯定涨」「必涨」「稳涨」「必然上涨」
  · 买卖指令：「建议买入」「推荐买入」「可以买」「建议卖出」「推荐卖出」「可以卖」
  · 炒作用语：「稳赚」「包赚」「抄底机会」「绝佳买点」「保证收益」「承诺收益」
  · 绝对化结论：「一定会涨」「肯定会涨」「涨定了」「必然」
  · 编造数据：用户未提供的任何价格、仓位比例、成交量数字

■ 输出风格
  · 中文 · 短句（每句不超过 20 字）· 要点化（bullet / 数字列表）
  · 面向普通股民，不使用专业黑话，不用感叹号表达乐观
  · 字数控制在 400–600 字（约 1 页纸），优先 Top 3–5 个要点

■ 证据要求
  · 每个关键判断必须在括号内或冒号后标注数据依据
    示例：「[根据用户数据：情绪偏多] 关注情绪驱动型板块」
  · 无对应数据时，必须直接写「数据不足，无法判断」，禁止编造替代内容

■ 不确定性要求
  · 每节至少出现 1 条不确定因素说明，格式：
    > 不确定：[具体说明为何无法确定方向/时点/结果]
  · 禁止写无条件的乐观/悲观绝对判断\
"""

# ── 高风险拦截 System Prompt ──────────────────────────────────────

_HIGH_RISK_SYSTEM = """\
你是专业的量化风险顾问。用户正在描述一个可能造成重大损失的极端操作意图。

你的职责：
1. 绝对不生成任何具体操作方案、仓位建议、价格计划
2. 用平静、非评判性的语气，清晰解释该操作的主要风险
3. 引导用户重新评估自己的风险承受能力

回复格式（直接输出对话文字，不要 JSON 格式）：
- 先用 1 句话表示理解用户的想法
- 再列出 3 个主要风险（基于通用市场知识，不编造具体数据）
- 最后提 1 个问题帮助用户自我评估
- 总字数 150 字以内，语气专业但不说教\
"""

# ── 高风险兜底模板（LLM 调用失败时使用）─────────────────────────

HIGH_RISK_REPLY_TEMPLATE = (
    "⚠️ 检测到高风险操作描述（「{phrase}」），已暂停生成执行方案。\n\n"
    "这类操作通常面临三个主要风险：\n"
    "1. **单次亏损过大**：全仓操作若判断失误，损失无法通过分散降低。\n"
    "2. **情绪决策放大**：极端仓位会加剧紧张情绪，导致更难理性止损。\n"
    "3. **无回旋空间**：一旦被套，没有资金可以平均成本或对冲。\n\n"
    "请先思考：**您的最大可承受损失是多少？** 确认后再继续讨论风控方案。"
)


# ══════════════════════════════════════════════════════════════════
# System Prompts
# ══════════════════════════════════════════════════════════════════

_MORNING_SYSTEM = """\
你是专业的中国A股量化投资副驾驶，帮助用户生成每日开盘前晨报。

【核心约束 — 必须严格遵守】
1. 只分析用户清单里出现的股票，不添加任何未提及的股票
2. 风险描述必须基于用户数据中可见的客观因素（情绪/板块/事件/公告），不虚构任何数据
3. 「今天你能做的 3 个动作」只写观察/验证/风控类操作，严禁给出买卖价位或入场建议
4. 若关键字段缺失（如无股票代码），先在 reply 中用中文追问，草稿相应节写"（待补充）"
5. 四节内容必须完整，节标题完全不可更改

用户今日数据：
{data_context}

每次回复必须直接输出严格 JSON（不加任何 ``` 标记，直接从 {{ 开始）：
{{"draft": "完整Markdown晨报草稿", "reply": "给用户的简短中文对话（1-2句）", "done": false}}

草稿格式（节标题必须与下方完全一致，一字不差）：

## 今日一句话概况
（一句话：今日市场情绪 + 关注板块 + 最重要的1个信息摘要或事件提示）

## 自选股关注清单
| 股票代码 | 今日关注要点 | 注意事项 |
|---------|-----------|---------|
（最多10行；根据用户备注、市场情绪、板块特点写简短有针对性的分析；若无股票写"| 待补充 | — | 请补充关注股票 |"）

## 今日 3 个风险
1. **[风险名]**：[一句来自用户数据的客观证据或现象]。[一句不确定性说明：指出无法判断的方向或时点]。
2. **[风险名]**：[证据]。[不确定性]。
3. **[风险名]**：[证据]。[不确定性]。

## 今天你能做的 3 个动作
1. 观察：[具体观察对象 + 观察什么信号，例：「观察600519开盘30分钟成交量是否明显放量」]
2. 验证：[验证什么市场逻辑或指标，例：「验证白酒ETF今日资金流向是否净流入」]
3. 风控：[具体风控措施，例：「确认所有持仓已设好止损提醒；今日新仓位不超过总资金X%」]

---
*⚠️ 本报告仅供参考，不构成投资建议，买卖决策请自行判断。*

done=true 条件：四节内容均完整，且 messages 中已有至少 1 条用户消息。\
"""

_RISK_SYSTEM = """\
你是专业的中国A股量化投资副驾驶，帮助用户进行持仓风控检查。

【核心约束 — 必须严格遵守】
1. 若数据中出现「⚠️ 规则引擎警告」，必须在「计划摘要」中单独加粗标注，不可忽视
2. 6 个问题的确认状态严格按轮次演变：初始全❓→第1轮后前3题✅→第2轮后全部✅
3. 「最容易亏钱的 3 点」必须结合用户的实际持仓具体分析，不允许泛泛说教
4. done=true 仅当用户回复轮数 ≥ 2 且 6 个问题全部标记为 ✅ 时才能设置

用户持仓数据：
{data_context}

每次回复必须直接输出严格 JSON（不加任何 ``` 标记，直接从 {{ 开始）：
{{"draft": "完整Markdown风控报告", "reply": "给用户的中文追问或确认（1-2句）", "done": false}}

草稿格式（节标题必须与下方完全一致）：

## 计划摘要
| 参数 | 设定值 |
|-----|-------|
| 最大回撤限制 | {max_dd}% |
| 个股止损线 | {stop_loss}% |
| 持仓数量 | {n_pos}只 |
（若有规则引擎警告，在表格下方单独加一段：**⚠️ 高风险标的**：XXX 当前已触及止损线，需立即决策。）

## 需要确认的 6 个问题
1. **止损执行**：触及 {stop_loss}% 止损线时，立刻止损还是继续持有？→ ❓/✅
2. **组合回撤**：总回撤达到 {max_dd}% 时，是否停止当天交易？→ ❓/✅
3. **仓位上限**：当前持仓中占比最高的是哪只？是否超出了您的单笔仓位上限？→ ❓/✅
4. **重大事件**：持仓中哪些股票近期有财报/解禁/重大政策节点？→ ❓/✅
5. **止损提醒**：{n_pos}只持仓是否全部在券商 App 设好了价格提醒？→ ❓/✅
6. **应急计划**：若明日大盘跌幅超过 2%，您的第一个动作是什么？→ ❓/✅

## 最容易亏钱的 3 点
（必须基于用户当前的具体持仓数据来分析，指出最可能导致亏损的行为模式）
1. **[亏损模式]**：[结合持仓实情说明原因]。应对：[具体可操作的措施]。
2. **[亏损模式]**：[原因]。应对：[措施]。
3. **[亏损模式]**：[原因]。应对：[措施]。

## 当前可做风控动作
（具体到用户的股票代码和参数，不要写泛化建议）
- [动作1]
- [动作2]
- [动作3]

---
*⚠️ 本报告仅供参考，不构成投资建议。*\
"""

_REVIEW_SYSTEM = """\
你是专业的中国A股量化投资副驾驶，帮助用户进行收盘复盘。

【核心约束 — 必须严格遵守】
1. 「做得好的 2 点」必须基于实际交易数据，不要空洞称赞
2. 「要改的 1 点」只给 1 点，要非常具体，直接指出问题行为，不泛化
3. 「执行 vs 认知」一句话，格式：今日情绪[X]下，[实际操作行为]——[符合/偏离]预设计划。[一句原因]。
4. 「明天 3 条风险提醒」基于今日操作的具体风险，不要写通用建议
5. 若情绪为「过于冲动」/「恐慌」/「贪婪」，必须在 reply 中追问心理状态，done 不能提前设 true

用户今日数据：
{data_context}

每次回复必须直接输出严格 JSON（不加任何 ``` 标记，直接从 {{ 开始）：
{{"draft": "完整Markdown复盘报告", "reply": "给用户的简短中文引导（1-2句）", "done": false}}

草稿格式（节标题必须与下方完全一致）：

## 做得好的 2 点
1. [具体正向行为，例：「在XXX触及止损位时，按计划及时执行了止损，没有心存侥幸」]
2. [另一个具体正向行为]

## 要改的 1 点（具体）
[只写 1 点；格式：今天在[具体场景]中，[做了什么行为]，可能导致[什么风险]。下次应该[具体改进动作]。]

## 执行 vs 认知（一句判据）
今日情绪[情绪标签]下，[实际操作描述]——[符合/偏离]预设计划。[一句原因]。

## 明天 3 条风险提醒
1. [基于今日具体操作衍生的风险，例：「今日追高买入XXX，若明日开盘跌破X元需控制持仓」]
2. [风险提醒]
3. [风险提醒]

---
*⚠️ 本报告仅供参考，不构成投资建议。*

done=true 条件：四节内容完整，且至少 1 轮用户回复。
情绪为「冷静」/「略紧张」且数据完整时，1 轮后可设 done=true。
情绪为「过于冲动」/「恐慌」/「贪婪」时，需追问心理状态后才可设 done=true。
若无交易记录（空仓），「做得好的 2 点」第 1 点写空仓避险，其余节基于情绪和心得分析。\
"""


# ══════════════════════════════════════════════════════════════════
# 数据上下文格式化
# ══════════════════════════════════════════════════════════════════

def _fmt_morning(data: dict) -> str:
    sector = data.get("sector", "").strip() or "（未填写）"
    sentiment = data.get("sentiment", "中性")
    notes = data.get("notes", "").strip()
    news = data.get("news", "").strip()

    # Step2 确认后的自选股表格优先，否则回退到 Step1 文本
    confirmed: list[dict] = data.get("_confirmed_table", [])
    stocks_raw: str = data.get("stocks", "").strip()

    if confirmed:
        rows = []
        for row in confirmed[:10]:
            code = str(row.get("股票代码", "") or "").strip()
            note = str(row.get("备注（可选）", "") or "").strip()
            if code:
                rows.append(f"  {code}｜{note if note else '（无备注）'}")
        table_text = "\n".join(rows) or "  （Step2 表格为空）"
        n_stocks = len(rows)
    elif stocks_raw:
        codes = [s.strip() for s in stocks_raw.replace("，", ",").split(",") if s.strip()]
        table_text = "\n".join(f"  {c}｜（无备注）" for c in codes[:10]) or "  （无股票）"
        n_stocks = len(codes[:10])
    else:
        table_text = "  （未填写任何关注股票）"
        n_stocks = 0

    parts = [
        f"关注板块：{sector}",
        f"今日市场情绪判断：{sentiment}",
        f"今日自选股清单（Step2 已确认，共 {n_stocks} 只，格式：代码｜备注）：\n{table_text}",
    ]
    if notes:
        parts.append(f"特别关注事项：{notes}")
    if news:
        parts.append(f"【今日公告 / 新闻摘要（用户粘贴）】：\n{news}")

    # 标注缺失字段
    missing = []
    if n_stocks == 0:
        missing.append("关注股票（用户未填写，请在 reply 中追问股票代码）")
    if missing:
        parts.append("⚠️ 信息缺口（请在 reply 中追问）：" + "；".join(missing))

    return "\n\n".join(parts)


def _fmt_risk(data: dict) -> str:
    max_dd = float(data.get("max_drawdown", 10))
    stop_loss = float(data.get("stop_loss", 5))
    positions: list[dict] = data.get("positions", [])

    lines = []
    danger = []
    total_val = 0.0

    for p in positions:
        stock = str(p.get("stock", "")).strip()
        cost = float(p.get("cost") or 0)
        current = float(p.get("current") or 0)
        qty = int(p.get("qty") or 0)
        pnl = ((current - cost) / cost * 100) if cost > 0 else 0.0
        mkt_val = current * qty
        total_val += mkt_val

        if pnl <= -stop_loss:
            badge = "🔴 超止损"
            danger.append(f"{stock}（盈亏 {pnl:+.1f}%）")
        elif pnl < 0:
            badge = "🟡 浮亏"
        elif pnl >= 5:
            badge = "🟢 盈利"
        else:
            badge = "⚪ 微盈"

        lines.append(
            f"  {stock}：成本 {cost:.2f} / 现价 {current:.2f} / "
            f"{qty}股 / 盈亏 {pnl:+.1f}% ({badge}) / 市值 {mkt_val:,.0f}元"
        )

    pos_text = "\n".join(lines) if lines else "  （未录入任何持仓）"
    result_parts = [
        f"最大回撤限制：{max_dd}%",
        f"个股止损线：{stop_loss}%",
        f"持仓列表（{len(positions)}只，合计市值约 {total_val:,.0f}元）：\n{pos_text}",
    ]

    if danger:
        result_parts.append(
            "⚠️ 规则引擎警告 — 以下标的已触及止损线，需立即决策：\n  "
            + "\n  ".join(danger)
        )

    return "\n\n".join(result_parts)


def _fmt_review(data: dict) -> str:
    emotion = data.get("emotion", "冷静")
    on_plan = data.get("on_plan", "不确定")
    notes = data.get("notes", "").strip() or "（未填写）"
    trades: list[dict] = data.get("trades", [])

    lines = []
    buy_val = sell_val = 0.0
    for t in trades:
        direction = "买入" if t.get("direction") == "buy" else "卖出"
        price = float(t.get("price") or 0)
        qty = int(t.get("qty") or 0)
        amount = price * qty
        if t.get("direction") == "buy":
            buy_val += amount
        else:
            sell_val += amount
        lines.append(
            f"  {t.get('stock', '')}：{direction} {qty}股 @ {price:.2f}元"
            f"（成交额 {amount:,.0f}元）"
        )

    n_buy = sum(1 for t in trades if t.get("direction") == "buy")
    n_sell = len(trades) - n_buy
    trade_text = "\n".join(lines) if lines else "  （今日空仓，无交易记录）"

    summary = (
        f"共 {len(trades)} 笔：买入 {n_buy} 笔（{buy_val:,.0f}元）"
        f"/ 卖出 {n_sell} 笔（{sell_val:,.0f}元）"
        if trades
        else "今日空仓"
    )

    return (
        f"今日情绪：{emotion}\n"
        f"按计划执行：{on_plan}\n"
        f"交易概况：{summary}\n"
        f"交易明细：\n{trade_text}\n"
        f"心得/反思：{notes}"
    )


# ══════════════════════════════════════════════════════════════════
# 公开接口：构建 System Prompt
# ══════════════════════════════════════════════════════════════════

def build_system_prompt(module: str, data: dict) -> str:
    """构建指定模块的 System Prompt（含专业约束层 + 完整数据上下文）。"""
    if module == "morning":
        base = _MORNING_SYSTEM.format(data_context=_fmt_morning(data))

    elif module == "risk":
        stop_loss = float(data.get("stop_loss", 5))
        max_dd = float(data.get("max_drawdown", 10))
        n_pos = len(data.get("positions", []))
        base = _RISK_SYSTEM.format(
            data_context=_fmt_risk(data),
            stop_loss=stop_loss,
            max_dd=max_dd,
            n_pos=n_pos,
        )

    elif module == "review":
        base = _REVIEW_SYSTEM.format(data_context=_fmt_review(data))

    else:
        base = (
            f"你是量化投资助手，模块：{module}。"
            '请以JSON格式返回{"draft":"...","reply":"...","done":false}。'
        )

    # 专业约束层统一注入（凌驾于模块约束之上）
    return _PROFESSIONAL_GUARDRAILS + "\n\n" + base


# ══════════════════════════════════════════════════════════════════
# 公开接口：构建 API Messages
# ══════════════════════════════════════════════════════════════════

def build_api_messages(messages: list[dict]) -> list[dict]:
    """
    将现有对话记录转换为 OpenAI API 格式。

    现有 messages 以 assistant 开头（第一条是 AI 的初始回复）。
    在前面插入一条 user 触发消息，使序列符合 user-first 规范。
    """
    trigger = {"role": "user", "content": "请根据上述数据生成初始草稿，并开始对话。"}
    if not messages:
        return [trigger]

    api_msgs: list[dict] = [trigger]
    for m in messages:
        role = m.get("role", "")
        if role in ("user", "assistant"):
            api_msgs.append({"role": role, "content": m.get("content", "")})
    return api_msgs


# ══════════════════════════════════════════════════════════════════
# 公开接口：解析 LLM 响应
# ══════════════════════════════════════════════════════════════════

def parse_llm_response(text: str, module: str, messages: list[dict]) -> dict:
    """
    解析 LLM JSON 响应为 {draft, reply, done}。

    Args:
        text:     LLM 返回的原始文本（期望为 JSON）
        module:   "morning" | "risk" | "review"
        messages: 已有对话记录（用于强制执行最少轮次规则）

    Returns:
        {"draft": str, "reply": str, "done": bool}
    """
    n_user = sum(1 for m in messages if m.get("role") == "user")
    raw = text.strip()

    # 去除可能的代码块包装（LLM 有时会加 ```json ... ```）
    for pat in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"):
        m = re.search(pat, raw)
        if m:
            raw = m.group(1).strip()
            break

    # 若响应以非 { 开头，尝试找第一个 { 开始
    brace_idx = raw.find("{")
    if brace_idx > 0:
        raw = raw[brace_idx:]

    try:
        obj = json.loads(raw)
        draft = str(obj.get("draft") or "").strip()
        reply = str(obj.get("reply") or "").strip()
        done = bool(obj.get("done", False))

        # 强制业务最少轮次约束（防止 LLM 提前设 done=true）
        min_rounds: dict[str, int] = {"morning": 1, "risk": 2, "review": 1}
        if n_user < min_rounds.get(module, 1):
            done = False

        return {
            "draft": draft or raw,
            "reply": reply or "草稿已更新，请查看左侧内容。",
            "done": done,
        }

    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "LLM 响应 JSON 解析失败（module=%s，n_user=%d），将原始文本作为草稿",
            module,
            n_user,
        )
        return {
            "draft": raw,
            "reply": "（AI 返回格式异常，内容已直接展示在左侧，可继续对话或手动编辑）",
            "done": False,
        }


# ══════════════════════════════════════════════════════════════════
# 公开接口：质量保障层辅助函数
# ══════════════════════════════════════════════════════════════════

def build_strict_retry_system_prompt(
    module: str,
    data: dict,
    issues: list[str],
) -> str:
    """
    构建加强约束的重试 system prompt（首次校验失败时使用）。

    在原有 system prompt 前注入明确的失败原因和更严格的约束。
    """
    issues_text = (
        "\n".join(f"  - {i}" for i in issues)
        if issues
        else "  - 上次输出质量不达标（具体原因未知）"
    )
    banned_sample = "「一定涨」「建议买入」「稳赚」「抄底机会」「推荐买入」"

    strict_prefix = f"""\
⚠️【重试约束 — 上次输出未通过质量校验，本次必须严格修正】

上次失败原因：
{issues_text}

本次必须做到（违反则再次失败）：
1. 所有必要节标题必须一字不差地完整出现
2. 绝对禁止以下违规表达：{banned_sample}（及类似措辞）
3. 每个关键判断必须有明确数据依据，无数据必须写「数据不足，无法判断」
4. 必须包含至少 1 条不确定因素描述（格式：> 不确定：[说明]）
5. 严格控制字数在 600 字以内，优先 Top 3–5 要点

"""
    base = build_system_prompt(module, data)
    return strict_prefix + base


def build_compress_messages(draft: str) -> list[dict]:
    """
    构建二次压缩调用的 messages（不使用 system prompt，直接 one-shot）。

    压缩原则：
    - 保留所有 ## 节标题（一字不改）
    - 每节保留最重要的 2–3 个要点，删除冗余说明
    - 不新增任何事实或数据
    - 不改变任何数字（止损线/仓位/盈亏%等）
    - 输出纯 Markdown，不加 JSON 包装
    """
    compress_prompt = f"""\
你是专业文档精简助手。请将以下量化投资分析报告压缩到约 500 字以内。

要求：
- 保留所有 ## 节标题（一字不改）
- 每节只保留最重要的 2–3 个要点，删除重复和冗余内容
- 不允许新增任何事实或数据
- 不允许修改任何数字（止损线/仓位/盈亏% 等）
- 保持中文、短句、要点化风格
- 输出纯 Markdown，不加 JSON 包装

待压缩报告：

{draft}"""
    return [{"role": "user", "content": compress_prompt}]


def build_high_risk_system_prompt(phrase: str) -> str:
    """
    构建高风险拦截时的 LLM system prompt。
    要求 LLM 解释风险，不生成任何执行方案。
    """
    return _HIGH_RISK_SYSTEM + f"\n\n用户提到的操作关键词：「{phrase}」"
