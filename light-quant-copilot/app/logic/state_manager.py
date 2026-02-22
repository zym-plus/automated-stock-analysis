"""
草稿自动暂存 + 自动恢复：st.session_state（内存）+ SQLite（持久化）双层机制。

持久化节点：
  Step2 确认 → 存 parsed_table + data
  Step3 生成/定稿 → 存 generated + messages
  Step4 导出 → 存 export 记录 + 标记 completed

恢复规则：
  进入模块时若 task_id 不在 session_state → 查 DB 最近 in_progress 任务
  显示横幅：继续 / 放弃并清空
"""
from __future__ import annotations

import streamlit as st

from .db import (
    create_task,
    update_task_step,
    set_task_status,
    upsert_task_state,
    load_task_state,
    find_latest_unfinished,
)

# 需要持久化的 session_state 键（wizard 全量状态）
_PERSIST_KEYS: tuple[str, ...] = (
    "step",
    "data",
    "generated",
    "messages",
    "locked",
    "parsed_table",
    "session_id",
    "qa_round",
    "qa_done",
)

# 各模块 Step3 对应的草稿 text_area widget key（chat_ui 中使用 {module}_draft_edit）
_MODULE_EDIT_KEY: dict[str, str] = {
    "morning": "morning_draft_edit",
    "risk":    "risk_draft_edit",
    "review":  "review_draft_edit",
}

_MODULE_LABEL: dict[str, str] = {
    "morning": "晨报",
    "risk": "风控",
    "review": "复盘",
}


# ──────────────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────────────

def _snapshot() -> dict:
    """从 session_state 提取持久化快照。"""
    return {k: st.session_state.get(k) for k in _PERSIST_KEYS}


def _restore(state: dict) -> None:
    """将快照写回 session_state；None 值用默认值代替。"""
    defaults: dict = {
        "step": 1,
        "data": {},
        "generated": "",
        "messages": [],
        "locked": False,
        "parsed_table": None,
        "session_id": None,
        "qa_round": 0,
        "qa_done": False,
    }
    for k in _PERSIST_KEYS:
        v = state.get(k)
        st.session_state[k] = v if v is not None else defaults.get(k)


# ──────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────

def sync_draft_to_generated(module: str) -> None:
    """
    切换模块前调用：把 Step3 text_area 的最新内容同步回 generated，
    确保 autosave 保存到的是用户的最新编辑。
    """
    edit_key = _MODULE_EDIT_KEY.get(module)
    if edit_key and edit_key in st.session_state:
        st.session_state.generated = st.session_state[edit_key]


def autosave(module: str, *, complete: bool = False) -> None:
    """
    将当前向导状态同步到 SQLite。
    - 若尚无 task_id 则自动创建任务。
    - complete=True 时将任务标记为 completed。
    - DB 操作失败时静默跳过，不影响 UI。
    """
    try:
        if "task_id" not in st.session_state:
            tid = create_task(module)
            st.session_state.task_id = tid
        else:
            tid = int(st.session_state.task_id)

        step = int(st.session_state.get("step", 1))
        update_task_step(tid, step)
        upsert_task_state(tid, _snapshot())
        if complete:
            set_task_status(tid, "completed")
    except Exception:
        pass   # 持久化失败静默跳过，不影响 UI


def maybe_offer_recovery(module: str) -> bool:
    """
    检查是否有未完成任务，并在 UI 顶部展示恢复横幅。
    返回 True → 横幅正在展示，调用方应立即 return，不继续渲染向导主体。
    DB 错误时静默跳过，返回 False。
    """
    # 当前 session 已有进行中任务 → 无需恢复
    if "task_id" in st.session_state:
        return False

    # 用户本次 session 已明确拒绝 → 不再提示
    if st.session_state.get("_recovery_declined"):
        return False

    # 尚未查过 DB → 查一次，结果缓存到 _pending_recovery
    if "_pending_recovery" not in st.session_state:
        try:
            task = find_latest_unfinished(module)
        except Exception:
            st.session_state._recovery_declined = True   # DB 异常，跳过恢复
            return False
        if task is None:
            st.session_state._recovery_declined = True  # 无任务，后续跳过
            return False
        st.session_state._pending_recovery = task

    _render_recovery_banner(st.session_state._pending_recovery)
    return True


def clear_module_state() -> None:
    """清除向导所有相关 session_state（切换模块时调用）。"""
    _STATIC_KEYS = [
        "task_id",
        "_pending_recovery",
        "_recovery_declined",
        # 共享图片解析结果
        "image_parsed_rows",
    ]

    # Step3 textarea widget keys
    _STEP3_EDIT_KEYS = list(_MODULE_EDIT_KEY.values())

    # Step2 data_editor + rows + notice（三个模块各一套）
    _STEP2_KEYS = [
        f"{m}_{suffix}"
        for m in ("morning", "risk", "review")
        for suffix in ("step2_rows", "step2_editor", "step2_notice")
    ]

    # 图片缓存 session_state keys（三个模块各一套）
    _IMAGE_KEYS = [
        f"{m}_{suffix}"
        for m in ("morning", "risk", "review")
        for suffix in ("img_info", "img_fp")
    ]

    keys_to_clear = (
        list(_PERSIST_KEYS)
        + _STATIC_KEYS
        + _STEP3_EDIT_KEYS
        + _STEP2_KEYS
        + _IMAGE_KEYS
    )
    for k in keys_to_clear:
        st.session_state.pop(k, None)


# ──────────────────────────────────────────────────────────
# 内部：渲染恢复横幅
# ──────────────────────────────────────────────────────────

def _render_recovery_banner(task: dict) -> None:
    updated = task.get("updated_at", "")[:16].replace("T", " ")
    step = task.get("step", 1)
    module_label = _MODULE_LABEL.get(task["module"], task["module"])

    st.warning(
        f"💾 **检测到未完成的任务** — "
        f"{module_label} · 第 {step} 步 · 最近操作：{updated}",
        icon=None,
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "▶ 继续上次任务",
            use_container_width=True,
            type="primary",
            key="_recovery_continue",
        ):
            try:
                state = load_task_state(task["id"])
            except Exception:
                state = None
            if state:
                _restore(state)
            st.session_state.task_id = task["id"]
            st.session_state.pop("_pending_recovery", None)
            st.rerun()
    with col2:
        if st.button(
            "🗑 放弃并清空",
            use_container_width=True,
            key="_recovery_abandon",
        ):
            try:
                set_task_status(task["id"], "abandoned")
            except Exception:
                pass
            st.session_state.pop("_pending_recovery", None)
            st.session_state._recovery_declined = True
            st.rerun()
