"""
AI 提供商抽象层。

实现：
  MockProvider   零配置占位（无需 Key，结构正确的模板输出）
  RealProvider   真实 LLM 接入（通过 llm/ 子包路由到 DeepSeek / Qwen 等）
  ProviderProxy  可切换后端的代理（保持模块级 default_provider 引用稳定）

路由逻辑：
  - 启动时自动检测 env vars；有 Key 则用 RealProvider，否则用 MockProvider
  - UI 修改设置后调用 reconfigure_provider(config) 热切换后端
  - RealProvider 调用失败时自动降级到 MockProvider（在 reply 中提示错误）
"""
from __future__ import annotations
from datetime import date


# 草稿软上限（约 1-2 页）；超出时触发规则压缩
_DRAFT_MAX_CHARS = 950


class BaseProvider:
    name: str = "BaseProvider"

    # ── 文本生成（旧接口，保留向后兼容）────────────────────────

    def generate_morning_report(self, data: dict) -> str:
        raise NotImplementedError

    def generate_risk_report(self, data: dict) -> str:
        raise NotImplementedError

    def generate_review(self, data: dict) -> str:
        raise NotImplementedError

    # ── Vision 接口 ─────────────────────────────────────────────

    @property
    def supports_vision(self) -> bool:
        """Provider 是否支持图片输入（Vision API）。"""
        return False

    def parse_image(self, module: str, image_bytes: bytes) -> list[dict]:
        """
        解析截图，返回结构化行列表。

        module: 'morning' | 'risk' | 'review'

        返回格式：
          morning → [{"stock_code": str, "note": str}, ...]
          risk    → [{"stock": str, "cost": float, "current": float, "qty": int}, ...]
          review  → [{"stock": str, "direction": str, "price": float, "qty": int}, ...]

        不支持 Vision 的实现应返回 []（不应抛出异常，保证降级可用）。
        """
        return []

    # ── 聊天式生成接口（新核心接口）────────────────────────────

    def chat(self, module: str, data: dict, messages: list[dict]) -> dict:
        """
        对话式生成/更新草稿。

        Args:
            module:   'morning' | 'risk' | 'review'
            data:     Step1/2 收集的输入数据
            messages: 已有聊天记录
                      格式：[{"role": "assistant"|"user", "content": str}, ...]

        Returns:
            {
              "draft": str,    # 当前最新草稿（Markdown，≤950字）
              "reply": str,    # AI 本轮回复（追问 / 确认 / 收尾）
              "done":  bool,   # True = AI 不再追问，用户可定稿
            }

        约束：
          - 禁止荐股或承诺收益
          - 缺数据必须用平实语言声明并追问
          - 输出默认 1 页（最多 2 页）
        """
        raise NotImplementedError


class MockProvider(BaseProvider):
    """
    零配置占位提供商。
    输出结构与真实 AI 一致，方便后续替换为 ClaudeProvider。
    """

    name = "MockProvider"

    # ── Vision 接口（MockProvider 无 Vision 能力，降级返回空列表）──

    @property
    def supports_vision(self) -> bool:
        return False

    def parse_image(self, module: str, image_bytes: bytes) -> list[dict]:
        """MockProvider 不具备 Vision 能力，返回空列表触发手动填写路径。"""
        return []

    # ──────────────────────────────────────────────────────────
    # 聊天式生成（核心实现）
    # ──────────────────────────────────────────────────────────

    def chat(self, module: str, data: dict, messages: list[dict]) -> dict:
        """按模块分发到对应的聊天处理方法。"""
        n_user = sum(1 for m in messages if m["role"] == "user")
        if module == "morning":
            return self._chat_morning(data, messages, n_user)
        elif module == "risk":
            return self._chat_risk(data, messages, n_user)
        elif module == "review":
            return self._chat_review(data, messages, n_user)
        return {"draft": "（不支持的模块类型）", "reply": "系统错误，请刷新重试。", "done": True}

    # ── 晨报聊天（max 2 轮用户回复）────────────────────────────

    def _chat_morning(self, data: dict, messages: list[dict], n_user: int) -> dict:
        stocks_str = data.get("stocks", "").strip()
        codes = [s.strip() for s in stocks_str.replace("，", ",").split(",") if s.strip()] if stocks_str else []
        sector = data.get("sector", "").strip() or "（未填写板块）"
        sentiment = data.get("sentiment", "中性")
        notes = data.get("notes", "").strip()

        draft = self._compress(self._morning_draft(codes, sector, sentiment, notes, messages, n_user))

        # ── 缺数据追问 ─────────────────────────────────────────
        if n_user == 0:
            missing_q = []
            if not codes:
                missing_q.append("**1.** 您今天关注哪些股票？填股票代码就行（比如 600519、000001）")
            if not notes:
                missing_q.append("**2.** 今天有没有您特别担心的消息或事件？（没有就说没有）")

            if missing_q:
                reply = (
                    "初稿已生成！有几处信息不太完整，补充后我会立刻更新：\n\n"
                    + "\n\n".join(missing_q)
                )
            else:
                reply = (
                    "初稿已生成！请帮我确认两个地方：\n\n"
                    "**1.** 自选股里，今天您最想重点盯的是哪一只？为什么？\n\n"
                    "**2.** 有没有今天特别担心的风险或消息？（比如财报、政策消息等）"
                )
            done = False

        elif n_user == 1:
            reply = (
                "收到，已把您的信息更新到报告里。\n\n"
                "如果还有补充（比如仓位计划、关键价位等）请告诉我；"
                "觉得可以了就直接点**「定稿」**。"
            )
            done = False

        else:
            reply = "报告已完善，建议您现在**定稿**，开始今天的交易日。"
            done = True

        return {"draft": draft, "reply": reply, "done": done}

    def _morning_draft(
        self,
        codes: list[str],
        sector: str,
        sentiment: str,
        notes: str,
        messages: list[dict],
        n_user: int,
    ) -> str:
        top = codes[0] if codes else "（待填写）"
        notes_action = f"关注：{notes[:25]}" if notes else "盘前查阅今日财经日历"
        supplement = self._last_user_quote(messages, n_user)

        rows = (
            "\n".join(f"| {c} | 待观察量价走势 | 自行设置止损位 |" for c in codes[:10])
            if codes
            else "| （未填写） | — | 请先录入关注股票 |"
        )

        # 每条风险：一句证据 + 一句不确定
        notes_evidence = f'"{notes[:20]}"相关事件尚未落地' if notes else "外部消息面随时可能变化"

        return f"""## 今日一句话概况
情绪**{sentiment}**，重点关注**{sector}**。谨慎操作，控制仓位。{supplement}

## 自选股关注清单（{len(codes)} 只）
| 代码 | 关注要点 | 建议 |
|------|---------|------|
{rows}

## 今日 3 个风险
1. **情绪风险**：{sentiment}情绪下近期换手率偏高，多空分歧加大。不确定催化剂具体时点，建议先观察。
2. **轮动风险**：{sector}板块近期资金流向不稳，出现内部分化。轮动方向尚未量能确认，勿追高。
3. **消息风险**：{notes_evidence}，自选股随时可能受消息影响。突发公告的方向和力度难以预判，提前设好提醒。

## 今天你能做的 3 个动作
1. **观察**：{top} 量价关系，重点看开盘前 30 分钟成交量。
2. **验证**：查 {sector} ETF 是否有资金净流入信号。
3. **风控**：{notes_action}；确认止损提醒已设置。

---
*⚠️ 仅为辅助分析框架，不构成投资建议，买卖决策请自行判断。*"""

    # ── 风控聊天（min 2 轮用户回复，必须追问 6 题）──────────────

    def _chat_risk(self, data: dict, messages: list[dict], n_user: int) -> dict:
        positions = data.get("positions", [])
        max_dd = data.get("max_drawdown", 10)
        stop_loss = data.get("stop_loss", 5)

        draft = self._compress(self._risk_draft(positions, max_dd, stop_loss, messages, n_user))

        # ── 缺数据追问 ─────────────────────────────────────────
        if n_user == 0:
            if not positions:
                reply = (
                    "我注意到持仓列表是空的。\n\n"
                    "**请告诉我**：您目前有哪些持仓？（说股票代码 + 大概成本价就行）\n\n"
                    "如果今天是空仓状态，直接告诉我，我帮您做空仓风控规划。"
                )
            else:
                reply = (
                    "风控初稿已生成！以下 **6 个问题**请逐一回答，\n"
                    "这样我才能帮您找出真正的风险点：\n\n"
                    "**1.** 触及止损线的标的，您打算立刻止损还是继续持有？\n"
                    "**2.** 组合整体回撤到 " + str(max_dd) + "% 时，您会停止当天交易吗？\n"
                    "**3.** 单笔最大仓位比例是多少？目前有没有超过这个比例的标的？\n"
                    "**4.** 近期是否有重大事件（财报、解禁）可能影响您的持仓？\n"
                    "**5.** 所有持仓是否已设置好止损提醒或自动止损？\n"
                    "**6.** 如果今天大盘大跌，您的第一个动作是什么？"
                )
            done = False

        elif n_user == 1:
            reply = (
                "明白了，已记录您的回答并更新了风控分析。\n\n"
                "再确认几个关键点：\n\n"
                "**A.** 您提到的止损计划，具体在什么价位执行？\n"
                "**B.** 如果明天继续下跌，您的仓位上限是多少？\n"
                "**C.** 今日最大的不确定因素是什么？"
            )
            done = False

        elif n_user == 2:
            reply = (
                "非常好，两轮追问已完成，风控分析已充分。\n\n"
                "📋 **核心风控结论已更新到草稿中**，建议现在**定稿**并导出。\n\n"
                "如还想补充其他风险因素，可以继续对话。"
            )
            done = True

        else:
            reply = "风控分析已充分完善，建议**定稿**。"
            done = True

        return {"draft": draft, "reply": reply, "done": done}

    def _risk_draft(
        self,
        positions: list[dict],
        max_dd: float,
        stop_loss: float,
        messages: list[dict],
        n_user: int,
    ) -> str:
        n = len(positions)
        red_cnt = sum(
            1 for p in positions
            if p.get("cost", 0) > 0 and (p.get("current", 0) - p.get("cost", 0)) / p.get("cost", 0) * 100 < -stop_loss
        )
        supplement = self._last_user_quote(messages, n_user)

        # 6 个问题的状态随对话轮次演变（让用户看到草稿在更新）
        def q(idx: int) -> str:
            if n_user == 0:
                return "→ **待确认** ❓"
            elif n_user == 1:
                return "→ ✅ 已补充（见对话）" if idx < 3 else "→ **待确认** ❓"
            else:
                return "→ ✅ 已确认"

        return f"""## 计划摘要
| 项目 | 值 |
|------|---|
| 最大回撤限制 | {max_dd}% |
| 个股止损线 | {stop_loss}% |
| 持仓只数 | {n} 只 |
| 触及止损线 | {red_cnt} 只 |{supplement}

## 需要确认的 6 个问题
1. **止损执行**：触及止损线时，是立刻止损还是继续持有？{q(0)}
2. **组合回撤**：总回撤达到 {max_dd}% 时，是否停止当天交易？{q(1)}
3. **仓位上限**：单笔最大仓位比例是多少？目前有没有超出的标的？{q(2)}
4. **重大事件**：近期是否有财报/解禁等可能影响持仓的事件？{q(3)}
5. **止损提醒**：所有持仓是否已设好止损提醒？{q(4)}
6. **应急计划**：若大盘大跌，您的第一个动作是什么？{q(5)}

## 最容易亏钱的 3 点
1. **不执行止损** → 浮亏越拖越大。应对：现在就设好止损提醒，别靠意志力。
2. **追跌补仓** → 越摊越深。应对：明确规定自己不追跌，写下来贴在屏幕旁。
3. **情绪化操作** → 恐慌杀跌/贪心追高。应对：只按预设计划执行，不临时起意。

## 你现在能做的风控动作
- 检查 {n} 只持仓是否全部设了止损提醒
- 计算总浮亏，确认是否接近 {max_dd}% 的组合回撤线
- 标记需要重点盯盘的标的，优先处理 🔴 高风险标的

---
*⚠️ 以上分析仅供参考，不构成投资建议。*"""

    # ── 复盘聊天（max 2 轮用户回复）────────────────────────────

    def _chat_review(self, data: dict, messages: list[dict], n_user: int) -> dict:
        trades = data.get("trades", [])
        emotion = data.get("emotion", "冷静")
        notes = data.get("notes", "").strip()

        draft = self._compress(self._review_draft(trades, emotion, notes, messages, n_user))

        # ── 缺数据追问 ─────────────────────────────────────────
        if n_user == 0:
            high_emotion = emotion in ("过于冲动", "恐慌", "贪婪")
            if high_emotion and not notes:
                reply = (
                    f"复盘初稿已生成。我注意到您今天情绪是「{emotion}」，\n\n"
                    "**能说说是什么情况吗？**（比如：被套了？追高了？反正就随便说说）\n\n"
                    "知道具体情况，复盘分析才能更准确。"
                )
            elif not trades:
                reply = (
                    "复盘初稿已生成。今天是空仓吗？\n\n"
                    "**1.** 今天没有操作，是因为没有合适机会，还是其他原因？\n\n"
                    "**2.** 如果明天有机会，您计划怎么做？"
                )
            else:
                n_buy = sum(1 for t in trades if t.get("direction") == "buy")
                n_sell = len(trades) - n_buy
                reply = (
                    f"复盘初稿已生成（今日 {len(trades)} 笔交易：买 {n_buy} 卖 {n_sell}）。\n\n"
                    "帮我核实两件事：\n\n"
                    "**1.** 今天最满意的一笔操作是哪个？为什么满意？\n\n"
                    "**2.** 有没有哪笔操作让您觉得可以做得更好？"
                )
            done = False

        elif n_user == 1:
            reply = (
                "谢谢！已将您的想法整合到复盘报告中。\n\n"
                "还有一个问题：**明天您打算做什么？**（有计划就说计划，没有就说观望）\n\n"
                "或者已经够了，可以直接**定稿**。"
            )
            done = False

        else:
            reply = "复盘报告已完善，建议**定稿**保存，为明天做好准备。"
            done = True

        return {"draft": draft, "reply": reply, "done": done}

    def _review_draft(
        self,
        trades: list[dict],
        emotion: str,
        notes: str,
        messages: list[dict],
        n_user: int,
    ) -> str:
        _emotion_insight = {
            "冷静":    "情绪稳定，执行有序，保持这种状态。",
            "略紧张":  "略有紧张，但总体可控；下次提前做好计划，会更从容。",
            "过于冲动": "过于冲动易产生计划外交易；下次开仓前先问：这在我的计划里吗？",
            "恐慌":    "恐慌易在低点止损；提前设好止损位，避免临时情绪决策。",
            "贪婪":    "贪婪易持仓过久或追高；设好止盈目标并严格执行。",
        }
        insight = _emotion_insight.get(emotion, "请观察今日情绪对决策的影响。")
        supplement = self._last_user_quote(messages, n_user)
        notes_ref = f'"{notes[:40]}"' if notes else "（未填写心得）"

        n_buy = sum(1 for t in trades if t.get("direction") == "buy")
        n_sell = len(trades) - n_buy

        # 做得好的 2 点：数据不足时说明
        if trades:
            good1 = (
                f"完成了 {len(trades)} 笔交易记录（买 {n_buy} 卖 {n_sell}），有完整记录可回溯。"
            )
        else:
            good1 = "今日空仓，避免了不必要的操作风险。（数据不足：无交易记录，以下分析基于情绪和心得）"

        # 明天风险提醒：数据不足时注明
        risk_note = "" if trades else "\n*（注：今日无交易记录，以下提醒基于通用市场原则）*"

        return f"""## 做得好的 2 点
1. {good1}
2. 坚持了复盘记录的习惯，有利于持续改进。{supplement}

## 要改的 1 点（具体）
**情绪管理**：{insight}
下次开仓前先自问：_这笔交易在我的计划里吗？_

## 执行 vs 认知（一句判据）
今日情绪「{emotion}」— {insight}
心得摘要：{notes_ref}

## 明天 3 条风险提醒{risk_note}
1. 若市场低开，先观察量能，不急于抄底。
2. 持仓标的若有关键支撑位，提前设好止损再睡觉。
3. 明日只按计划执行，不因今日表现影响判断。

---
*⚠️ 以上复盘仅为辅助框架，不构成投资建议。*"""

    # ── 内部工具 ───────────────────────────────────────────────

    @staticmethod
    def _last_user_quote(messages: list[dict], n_user: int) -> str:
        """提取最近一条用户消息，格式化为引用块。"""
        if n_user <= 0:
            return ""
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if not last:
            return ""
        excerpt = last[:60] + ("…" if len(last) > 60 else "")
        return f"\n\n> 📝 **您的补充**：{excerpt}"

    def _compress(self, draft: str) -> str:
        """若草稿超过软上限，规则裁剪（保留结构，截断正文）。"""
        if len(draft) <= _DRAFT_MAX_CHARS:
            return draft
        # 找最近换行位截断，不在标题行中间断
        cutoff = draft.rfind("\n", 0, _DRAFT_MAX_CHARS)
        if cutoff == -1:
            cutoff = _DRAFT_MAX_CHARS
        return draft[:cutoff] + "\n\n*（内容已压缩至约 1 页）*"

    # ──────────────────────────────────────────────────────────
    # 旧版文本生成（向后兼容，由 chat() 内部替代）
    # ──────────────────────────────────────────────────────────

    def generate_morning_report(self, data: dict) -> str:
        result = self.chat("morning", data, [])
        return result["draft"]

    def generate_risk_report(self, data: dict) -> str:
        result = self.chat("risk", data, [])
        return result["draft"]

    def generate_review(self, data: dict) -> str:
        result = self.chat("review", data, [])
        return result["draft"]

    def _mock_stock_rows(self, stocks_str: str) -> str:
        if not stocks_str:
            return "（未输入股票代码）"
        stocks = [s.strip() for s in stocks_str.replace("，", ",").split(",") if s.strip()]
        lines = []
        for stock in stocks[:8]:
            lines.append(
                f"**{stock}**\n"
                f"- 趋势：【待 AI 分析】\n"
                f"- 关键支撑位：--\n"
                f"- 关键阻力位：--\n"
            )
        return "\n".join(lines)

    def _risk_rows(self, positions: list[dict], stop_loss_pct: float) -> str:
        if not positions:
            return "（未录入持仓）"
        lines = []
        for p in positions:
            stock = p.get("stock", "")
            cost = float(p.get("cost") or 0)
            current = float(p.get("current") or 0)
            qty = int(p.get("qty") or 0)
            pnl_pct = ((current - cost) / cost * 100) if cost > 0 else 0.0
            risk = "🔴" if pnl_pct < -stop_loss_pct else ("🟡" if pnl_pct < 0 else "🟢")
            lines.append(
                f"- **{stock}**：{pnl_pct:+.1f}%，市值约 {current*qty:,.0f} 元 {risk}"
            )
        return "\n".join(lines)

    def _trade_rows(self, trades: list[dict]) -> str:
        if not trades:
            return "（今日无交易记录）"
        lines = []
        for t in trades:
            direction = "买入" if t.get("direction") == "buy" else "卖出"
            price = float(t.get("price") or 0)
            qty = int(t.get("qty") or 0)
            lines.append(
                f"- **{t.get('stock', '')}**：{direction} {qty} 股 @ {price:.2f} 元"
            )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# RealProvider — 调用真实 LLM，失败时降级到 MockProvider
# ══════════════════════════════════════════════════════════════════

class RealProvider(BaseProvider):
    """
    通过 llm.LLMRouter 调用真实 API 实现对话式草稿生成。
    任何 API 错误均降级到 MockProvider 输出，并在 reply 中提示原因。
    """

    name = "RealProvider"

    def __init__(self, router) -> None:  # router: llm.LLMRouter
        self._router = router
        self._mock = MockProvider()

    # ── Vision：暂不支持，TODO 后续可检查 router 的 active client ──

    @property
    def supports_vision(self) -> bool:
        return False

    def parse_image(self, module: str, image_bytes: bytes) -> list[dict]:
        return []

    # ── 核心：对话式草稿生成 ─────────────────────────────────────

    def chat(self, module: str, data: dict, messages: list[dict]) -> dict:
        # mock 模式（router 无有效 Key）→ 直接使用 MockProvider
        if self._router.is_mock:
            return self._mock.chat(module, data, messages)

        # 真实模式：走完整 LLM 流水线（校验 + 可能的重试/压缩 + 审计）
        from .llm.pipeline import LLMPipeline
        return LLMPipeline(self._router).run(module, data, messages)

    # ── 连通性测试 ────────────────────────────────────────────────

    def test_connection(self) -> dict:
        return self._router.test_active()

    # ── 旧接口兼容 ────────────────────────────────────────────────

    def generate_morning_report(self, data: dict) -> str:
        return self.chat("morning", data, [])["draft"]

    def generate_risk_report(self, data: dict) -> str:
        return self.chat("risk", data, [])["draft"]

    def generate_review(self, data: dict) -> str:
        return self.chat("review", data, [])["draft"]


# ══════════════════════════════════════════════════════════════════
# ProviderProxy — 可热切换后端，保持模块级引用稳定
# ══════════════════════════════════════════════════════════════════

class ProviderProxy(BaseProvider):
    """
    包装一个可切换的后端 Provider。
    所有模块通过 `from .providers import default_provider` 获取的引用
    指向同一个 ProviderProxy 实例；调用 set_backend() 可在不破坏引用的
    情况下热切换底层实现。
    """

    name = "ProviderProxy"

    def __init__(self, backend: BaseProvider) -> None:
        self._backend = backend

    def set_backend(self, backend: BaseProvider) -> None:
        self._backend = backend

    # ── 状态查询 ──────────────────────────────────────────────────

    @property
    def is_mock(self) -> bool:
        return isinstance(self._backend, MockProvider)

    @property
    def mode_label(self) -> str:
        """UI 显示用的模式标签。"""
        if isinstance(self._backend, MockProvider):
            return "🎭 演示模式（Mock）"
        if isinstance(self._backend, RealProvider):
            provider = self._backend._router.active_provider.upper()
            model = self._backend._router.active_model
            return f"✅ 真实分析：{provider}（{model}）"
        return self._backend.name

    # ── 代理到后端 ────────────────────────────────────────────────

    @property
    def supports_vision(self) -> bool:
        return self._backend.supports_vision

    def parse_image(self, module: str, image_bytes: bytes) -> list[dict]:
        return self._backend.parse_image(module, image_bytes)

    def chat(self, module: str, data: dict, messages: list[dict]) -> dict:
        return self._backend.chat(module, data, messages)

    def test_connection(self) -> dict:
        if isinstance(self._backend, MockProvider):
            return {"ok": True, "latency_ms": 0, "text": "演示模式（无需测试）", "error": ""}
        return self._backend.test_connection()

    def generate_morning_report(self, data: dict) -> str:
        return self._backend.generate_morning_report(data)

    def generate_risk_report(self, data: dict) -> str:
        return self._backend.generate_risk_report(data)

    def generate_review(self, data: dict) -> str:
        return self._backend.generate_review(data)


# ══════════════════════════════════════════════════════════════════
# 初始化 & 公开函数
# ══════════════════════════════════════════════════════════════════

def _build_initial_backend() -> BaseProvider:
    """根据 env vars 选择初始后端（有 Key → RealProvider，否则 MockProvider）。"""
    import os
    has_key = bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("QWEN_API_KEY"))
    if not has_key:
        return MockProvider()
    try:
        from .llm import get_router
        from .llm.config import load_config
        router = get_router(load_config())
        if not router.is_mock:
            return RealProvider(router)
    except Exception:
        pass
    return MockProvider()


def reconfigure_provider(config=None) -> None:
    """
    热切换 default_provider 的后端。

    Args:
        config: LLMConfig 实例；None 时从 env vars 重新读取。
    """
    from .llm import rebuild_router
    from .llm.config import load_config
    cfg = config or load_config()
    router = rebuild_router(cfg)
    if router.is_mock:
        default_provider.set_backend(MockProvider())
    else:
        default_provider.set_backend(RealProvider(router))


def reconfigure_from_settings(settings: dict) -> None:
    """
    从 settings.json（settings dict）热切换 default_provider 后端。
    由 ui.py 启动加载和 settings_ui.py 保存时调用。
    """
    from .settings import settings_to_llm_config
    cfg = settings_to_llm_config(settings)
    reconfigure_provider(cfg)


# 默认提供商代理（全局单例，通过 reconfigure_provider() 热切换后端）
default_provider: ProviderProxy = ProviderProxy(_build_initial_backend())
