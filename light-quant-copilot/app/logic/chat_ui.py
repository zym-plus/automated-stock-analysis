"""
Step3 共享聊天 UI。

render_chat_step3(module) 供三个模块的 _step3_generate() 直接调用。

约束：
  - 晨报 / 复盘：done=True 后即可定稿
  - 风控：必须 >= 2 轮用户回复（qa_round >= 2）才能定稿
  - 草稿随每轮聊天实时更新
  - 定稿后锁定（locked=True），输入框隐藏
"""
from __future__ import annotations

import streamlit as st

from .providers import default_provider
from .db import save_session, update_export, add_export
from .state_manager import autosave

# 各模块最低用户回复轮数（达到才允许定稿）
_MIN_ROUNDS: dict[str, int] = {
    "morning": 0,   # done 信号驱动
    "risk":    2,   # 强制至少 2 轮
    "review":  0,   # done 信号驱动
}

_MODULE_TITLE: dict[str, str] = {
    "morning": "Step 3：AI 生成晨报",
    "risk":    "Step 3：AI 风控分析",
    "review":  "Step 3：AI 生成复盘报告",
}

_MODULE_SPINNER: dict[str, str] = {
    "morning": "正在生成晨报初稿...",
    "risk":    "正在生成风控初稿...",
    "review":  "正在生成复盘初稿...",
}


# ------------------------------------------------------------------
# 内部辅助：数据增强
# ------------------------------------------------------------------

def _enrich_data(data: dict) -> dict:
    """
    把 Step2 确认后的 parsed_table 注入 data['_confirmed_table']，供 LLM 使用。

    对 morning 模块：parsed_table 是股票列表（含备注）。
    对 review 模块：parsed_table 是交易记录表格（供 LLM 引用原始表格行）。
    原始 data 不被修改，返回浅拷贝。
    """
    enriched = dict(data)
    pt = st.session_state.get("parsed_table")
    if pt:
        enriched["_confirmed_table"] = pt
    return enriched


# ------------------------------------------------------------------
# 公开入口
# ------------------------------------------------------------------

def render_chat_step3(module: str) -> None:
    """渲染 Step3 聊天界面（含草稿实时预览 + 定稿锁定）。"""
    st.subheader(_MODULE_TITLE.get(module, "Step 3：AI 生成"))

    data = st.session_state.get("data", {})
    enriched = _enrich_data(data)

    # ── 首次进入：生成初始草稿 ────────────────────────────────────
    if not st.session_state.get("generated"):
        try:
            with st.status(_MODULE_SPINNER.get(module, "正在生成..."), expanded=True) as _status:
                result = default_provider.chat(module, enriched, [])
                _status.update(label="✅ 草稿已生成", state="complete", expanded=False)
        except Exception as _gen_err:
            # 生成失败：构造一个降级结果，保证页面不崩
            result = {
                "draft": f"（初始生成失败：{type(_gen_err).__name__}，请尝试刷新或稍后重试）",
                "reply": f"⚠️ 草稿生成遇到异常：{str(_gen_err)[:100]}。您可以尝试刷新，或返回上一步重新进入。",
                "done":  False,
            }

        draft   = result["draft"]
        reply   = result["reply"]
        ai_done = result["done"]

        st.session_state.generated = draft
        st.session_state.messages  = [{"role": "assistant", "content": reply}]
        st.session_state.qa_round  = 0
        st.session_state.qa_done   = ai_done

        try:
            sid = save_session(module, data, draft)
            st.session_state.session_id = sid
        except Exception:
            pass   # DB 写入失败不影响 UI
        autosave(module)   # autosave 内部已有 try/except

    locked  = st.session_state.get("locked", False)
    qa_done = st.session_state.get("qa_done", False)
    qa_round = int(st.session_state.get("qa_round", 0))
    min_rounds = _MIN_ROUNDS.get(module, 0)

    # ── 布局：左列草稿 / 右列聊天 ──────────────────────────────
    col_draft, col_chat = st.columns([1, 1], gap="large")

    # ── 左列：草稿预览 ────────────────────────────────────────────
    with col_draft:
        st.markdown("#### 当前草稿")
        if locked:
            st.markdown(st.session_state.generated)
        else:
            draft_edit = st.text_area(
                "草稿内容（可直接编辑）",
                value=st.session_state.generated,
                height=420,
                key=f"{module}_draft_edit",
                label_visibility="collapsed",
            )
            # 实时同步编辑内容到 generated
            st.session_state.generated = draft_edit

    # ── 右列：聊天记录 + 输入 ─────────────────────────────────────
    with col_chat:
        st.markdown("#### AI 助手")
        _render_chat_history()

        if locked:
            st.success("报告已定稿。")
        else:
            _render_chat_input(module, enriched, qa_done, qa_round, min_rounds)

    st.divider()

    # ── 底部操作区 ────────────────────────────────────────────────
    can_finalize = locked or (qa_done and qa_round >= min_rounds)
    _render_bottom_buttons(module, can_finalize, locked)


# ------------------------------------------------------------------
# 内部：聊天记录
# ------------------------------------------------------------------

def _render_chat_history() -> None:
    messages: list[dict] = st.session_state.get("messages", [])

    # 若最新 AI 消息以 ⚠️ 开头，说明真实 AI 调用失败，额外显示警告框
    last_ai = next(
        (m for m in reversed(messages) if m.get("role") == "assistant"), None
    )
    if last_ai:
        first_line = last_ai.get("content", "").split("\n")[0]
        if first_line.startswith("⚠️"):
            st.warning(first_line)

    for msg in messages:
        role = msg.get("role", "assistant")
        content = msg.get("content", "")
        with st.chat_message(role):
            st.markdown(content)


# ------------------------------------------------------------------
# 内部：聊天输入
# ------------------------------------------------------------------

def _render_chat_input(
    module: str,
    enriched: dict,
    qa_done: bool,
    qa_round: int,
    min_rounds: int,
) -> None:
    """渲染聊天输入框；ai done 且已满足最低轮数时隐藏并提示定稿。"""
    ready_to_finalize = qa_done and qa_round >= min_rounds

    if ready_to_finalize:
        st.info("AI 已完成分析，您可以继续补充，也可以直接**定稿**。")

    user_input = st.chat_input(
        "输入您的回答或补充…",
        key=f"{module}_chat_input",
        disabled=False,  # 始终可输入，直到 locked
    )

    if user_input:
        _handle_user_message(module, enriched, user_input)


def _handle_user_message(module: str, enriched: dict, user_input: str) -> None:
    """将用户消息追加到历史，调用 provider.chat()，更新草稿。"""
    messages: list[dict] = st.session_state.get("messages", [])
    messages.append({"role": "user", "content": user_input})

    with st.spinner("AI 正在思考..."):
        result = default_provider.chat(module, enriched, messages)

    draft   = result["draft"]
    reply   = result["reply"]
    ai_done = result["done"]

    messages.append({"role": "assistant", "content": reply})
    st.session_state.messages   = messages
    # _keep_draft=True 表示高风险拦截或错误，不覆盖当前草稿
    if not result.get("_keep_draft"):
        st.session_state.generated  = draft
    st.session_state.qa_done    = ai_done
    st.session_state.qa_round   = int(st.session_state.get("qa_round", 0)) + 1

    autosave(module)
    st.rerun()


# ------------------------------------------------------------------
# 内部：底部按钮
# ------------------------------------------------------------------

def _render_bottom_buttons(module: str, can_finalize: bool, locked: bool) -> None:
    col1, col2 = st.columns(2)

    with col1:
        if st.button("← 返回修改输入", use_container_width=True):
            st.session_state.step      = 1
            st.session_state.generated = ""
            st.session_state.locked    = False
            st.session_state.messages  = []
            st.session_state.qa_round  = 0
            st.session_state.qa_done   = False
            st.session_state.pop("parsed_table", None)
            autosave(module)
            st.rerun()

    with col2:
        if locked:
            if st.button("← 解锁继续编辑", use_container_width=True):
                st.session_state.locked = False
                autosave(module)
                st.rerun()
        else:
            finalize_label = "定稿，准备导出 →"
            if st.button(
                finalize_label,
                use_container_width=True,
                type="primary",
                disabled=not can_finalize,
            ):
                st.session_state.locked = True
                st.session_state.step   = 4
                autosave(module)
                st.rerun()

    if not can_finalize and not locked:
        min_rounds = _MIN_ROUNDS.get(module, 0)
        remaining = max(0, min_rounds - int(st.session_state.get("qa_round", 0)))
        if remaining > 0:
            st.caption(
                f"💡 风控分析需要至少 {min_rounds} 轮对话才可定稿（还差 {remaining} 轮）。"
            )
        else:
            st.caption("💡 请继续与 AI 对话，完成追问后即可定稿。")
