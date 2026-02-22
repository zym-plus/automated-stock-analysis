"""
收盘复盘向导（4步 Wizard）。
Step1 录入交易（支持截图上传）→ Step2 可编辑交易表格 → Step3 AI总结 → Step4 导出

图片处理策略（无需 OCR）：
  - 上传图片 → Pillow 预处理（缩放 + 压缩）→ 显示优化信息
  - 若 provider.supports_vision → 调用 parse_image() 识别成交记录
  - 无论识别成功与否 → Step2 始终展示可编辑表格
  - 识别失败/无 vision → 表格用文本解析数据或空行填充，供手动录入
"""
from __future__ import annotations
from pathlib import Path
import math
import streamlit as st
import pandas as pd

from .providers import default_provider
from .export import export_review, open_folder
from .db import update_export, add_export, abandon_task_if_incomplete
from .state_manager import maybe_offer_recovery, autosave
from .image_utils import preprocess_image
from .chat_ui import render_chat_step3


# ── 辅助：安全类型转换 ─────────────────────────────────────────────

def _sf(v, default: float = 0.0) -> float:
    try:
        r = float(v)
        return default if math.isnan(r) else r
    except (TypeError, ValueError):
        return default


def _si(v, default: int = 0) -> int:
    return int(_sf(v, default))


# ------------------------------------------------------------------
# 公开入口
# ------------------------------------------------------------------

def render_review_wizard() -> None:
    st.title("📊 收盘复盘向导")

    if maybe_offer_recovery("review"):
        return

    _render_steps_bar()
    st.divider()

    step = st.session_state.get("step", 1)
    if step == 1:
        _step1_input()
    elif step == 2:
        _step2_confirm()
    elif step == 3:
        _step3_generate()
    elif step == 4:
        _step4_export()


# ------------------------------------------------------------------
# 步骤指示器
# ------------------------------------------------------------------

def _render_steps_bar() -> None:
    labels = ["1 录入交易", "2 核对数据", "3 AI 总结", "4 导出"]
    step = st.session_state.get("step", 1)
    cols = st.columns(4)
    for i, (col, label) in enumerate(zip(cols, labels)):
        with col:
            num = i + 1
            if num == step:
                st.markdown(f"**:blue[{label}]**")
            elif num < step:
                st.markdown(f"~~{label}~~ ✓")
            else:
                st.markdown(label)


# ------------------------------------------------------------------
# 辅助：解析交易文本
# ------------------------------------------------------------------

def _parse_trades(text: str) -> list[dict]:
    result = []
    if not text.strip():
        return result
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.replace("，", ",").split(",")]
        if len(parts) < 4 or not parts[0]:
            continue
        try:
            direction = parts[1].lower()
            if direction in ("买", "买入"):
                direction = "buy"
            elif direction in ("卖", "卖出"):
                direction = "sell"
            elif direction not in ("buy", "sell"):
                direction = "buy"
            result.append({
                "stock": parts[0],
                "direction": direction,
                "price": float(parts[2]),
                "qty": int(parts[3]),
            })
        except ValueError:
            pass
    return result


# ------------------------------------------------------------------
# Step 1：录入交易（+ 可选截图上传）
# ------------------------------------------------------------------

def _step1_input() -> None:
    st.subheader("Step 1：录入今日交易与心得")

    # ── 截图上传区 ────────────────────────────────────────────────
    with st.expander("📸 上传成交记录截图自动识别（可选，无需 OCR）", expanded=False):
        uploaded = st.file_uploader(
            "支持 JPG / PNG / BMP / WebP",
            type=["jpg", "jpeg", "png", "bmp", "webp"],
            key="review_img_upload",
        )
        if uploaded:
            fp = f"{uploaded.name}_{uploaded.size}"
            if st.session_state.get("review_img_fp") != fp:
                with st.spinner("正在优化图片..."):
                    info = preprocess_image(uploaded)
                st.session_state.review_img_info = info
                st.session_state.review_img_fp = fp

            info = st.session_state.review_img_info
            ow, oh = info["original_size"]
            nw, nh = info["optimized_size"]
            col_img, col_info = st.columns([2, 3])
            with col_img:
                st.image(str(info["path"]), use_container_width=True)
            with col_info:
                st.success(
                    f"✅ 已自动优化  \n"
                    f"{ow}×{oh}px / {info['size_kb_original']}KB"
                    f" → {nw}×{nh}px / {info['size_kb_optimized']}KB"
                )
                if default_provider.supports_vision:
                    st.info("Vision AI 将在下一步自动识别成交记录")
                else:
                    st.caption("当前 Provider 无 Vision 能力，图片已优化缓存；请在 Step2 手动核对数据。")

    prev = st.session_state.get("data", {})

    # ── 文字录入表单 ──────────────────────────────────────────────
    with st.form("review_step1"):
        st.markdown("**今日交易记录**（每行一笔，格式：代码,买卖方向,价格,数量）")
        st.caption("买入方向填 buy，卖出方向填 sell。示例：600519,buy,1850,100")
        trades_text = st.text_area(
            "交易记录（今日无交易可留空）",
            value=prev.get("trades_raw", ""),
            placeholder="600519,buy,1850,100\n000001,sell,13.20,500",
            height=120,
        )
        emotion = st.selectbox(
            "今日交易情绪",
            ["冷静", "略紧张", "过于冲动", "恐慌", "贪婪"],
            index=["冷静", "略紧张", "过于冲动", "恐慌", "贪婪"].index(
                prev.get("emotion", "冷静")
            ),
        )
        on_plan = st.radio(
            "今日交易是否按计划执行？",
            ["是", "否", "不确定"],
            index=["是", "否", "不确定"].index(prev.get("on_plan", "不确定")),
            horizontal=True,
        )
        notes = st.text_area(
            "今日交易心得 / 反思",
            value=prev.get("notes", ""),
            placeholder="今天操作了……感觉……下次应该……",
            height=100,
        )
        submitted = st.form_submit_button("下一步 →", use_container_width=True, type="primary")

    if submitted:
        trades = _parse_trades(trades_text)
        st.session_state.data = {
            "trades": trades,
            "trades_raw": trades_text,
            "emotion": emotion,
            "on_plan": on_plan,
            "notes": notes.strip(),
        }
        st.session_state.step = 2
        st.session_state.generated = ""
        st.session_state.locked = False

        img_info = st.session_state.get("review_img_info")
        if img_info:
            with st.spinner("正在识别截图内容..."):
                parsed = default_provider.parse_image("review", img_info["bytes"])
            st.session_state.image_parsed_rows = parsed or []
        else:
            st.session_state.pop("image_parsed_rows", None)

        st.session_state.pop("review_step2_rows", None)
        st.session_state.pop("review_step2_editor", None)
        st.rerun()


# ------------------------------------------------------------------
# Step 2：可编辑交易表格 + 成交汇总
# ------------------------------------------------------------------

_DIRECTIONS = ["买入", "卖出"]
_DIR_MAP = {"buy": "买入", "sell": "卖出", "买入": "买入", "卖出": "卖出"}
_DIR_REV = {"买入": "buy", "卖出": "sell"}


def _step2_confirm() -> None:
    st.subheader("Step 2：核对今日交易数据")

    data = st.session_state.data

    # ── 初始化可编辑行 ────────────────────────────────────────────
    if "review_step2_rows" not in st.session_state:
        if pt := st.session_state.get("parsed_table"):
            rows = pt
            st.session_state.review_step2_notice = "💾 已从存档恢复交易数据，请核对后确认。"
        else:
            image_rows: list[dict] = st.session_state.pop("image_parsed_rows", None) or []
            if image_rows:
                rows = [
                    {
                        "股票": r.get("stock", ""),
                        "方向": _DIR_MAP.get(r.get("direction", "buy"), "买入"),
                        "价格": float(r.get("price", 0) or 0),
                        "数量": int(float(r.get("qty", 0) or 0)),
                    }
                    for r in image_rows
                ]
                cnt = len([r for r in rows if r["股票"]])
                st.session_state.review_step2_notice = (
                    f"✅ 已从截图识别 {cnt} 笔交易，请核对后确认。"
                )
            else:
                trades = data.get("trades", [])
                rows = [
                    {
                        "股票": t["stock"],
                        "方向": _DIR_MAP.get(t["direction"], "买入"),
                        "价格": float(t["price"]),
                        "数量": int(t["qty"]),
                    }
                    for t in trades
                ]
                st.session_state.pop("review_step2_notice", None)
        if not rows:
            rows = [{"股票": "", "方向": "买入", "价格": 0.0, "数量": 0}]
        st.session_state.review_step2_rows = rows

    if notice := st.session_state.get("review_step2_notice"):
        st.info(notice)

    # ── 情绪与心得（只读）────────────────────────────────────────
    col_e, col_p, col_n = st.columns([1, 1, 2])
    col_e.metric("今日情绪", data.get("emotion", ""))
    col_p.metric("按计划执行", data.get("on_plan", "不确定"))
    notes_preview = (data.get("notes") or "（未填写）")[:50]
    col_n.metric("心得摘要", notes_preview)
    st.markdown("---")

    # ── 可编辑交易表格 ────────────────────────────────────────────
    st.markdown("**交易记录**（可直接编辑、添加或删除行）")
    initial_df = pd.DataFrame(st.session_state.review_step2_rows)

    edited_df = st.data_editor(
        initial_df,
        num_rows="dynamic",
        width="stretch",
        key="review_step2_editor",
        column_config={
            "股票": st.column_config.TextColumn(
                "股票代码", required=True, max_chars=10
            ),
            "方向": st.column_config.SelectboxColumn(
                "买卖方向", options=_DIRECTIONS, required=True
            ),
            "价格": st.column_config.NumberColumn(
                "价格", format="%.2f", min_value=0.0, step=0.01
            ),
            "数量": st.column_config.NumberColumn(
                "数量（股）", format="%d", min_value=0, step=1
            ),
        },
    )

    # ── 实时成交金额汇总 ──────────────────────────────────────────
    summary_rows = []
    for _, row in edited_df.iterrows():
        stock = str(row.get("股票") or "").strip()
        if not stock:
            continue
        direction_label = str(row.get("方向") or "买入")
        price = _sf(row.get("价格"))
        qty = _si(row.get("数量"))
        amount = price * qty
        summary_rows.append({
            "股票": stock,
            "方向": ("🟢 买入" if direction_label == "买入" else "🔴 卖出"),
            "价格": f"{price:.2f}",
            "数量": qty,
            "成交金额": f"{amount:,.0f}",
        })

    if summary_rows:
        with st.expander("💰 成交汇总（实时）", expanded=True):
            st.dataframe(
                pd.DataFrame(summary_rows), width="stretch", hide_index=True
            )

    # ── 去重 ──────────────────────────────────────────────────────
    col_dedup, _ = st.columns([1, 4])
    with col_dedup:
        if st.button("一键去重", key="review_dedup"):
            deduped = edited_df.drop_duplicates().reset_index(drop=True)
            st.session_state.review_step2_rows = deduped.to_dict("records")
            st.session_state.pop("review_step2_editor", None)
            st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回修改", use_container_width=True):
            st.session_state.pop("review_step2_rows", None)
            st.session_state.pop("review_step2_editor", None)
            st.session_state.pop("review_step2_notice", None)
            st.session_state.step = 1
            st.rerun()
    with col2:
        if st.button("确认，生成复盘 →", use_container_width=True, type="primary"):
            trades = []
            for _, row in edited_df.iterrows():
                stock = str(row.get("股票") or "").strip()
                if not stock:
                    continue
                direction_label = str(row.get("方向") or "买入")
                trades.append({
                    "stock": stock,
                    "direction": _DIR_REV.get(direction_label, "buy"),
                    "price": _sf(row.get("价格")),
                    "qty": _si(row.get("数量")),
                })
            st.session_state.data["trades"] = trades
            st.session_state.parsed_table = edited_df.to_dict("records")
            st.session_state.pop("review_step2_rows", None)
            st.session_state.pop("review_step2_editor", None)
            st.session_state.pop("review_step2_notice", None)
            st.session_state.step = 3
            autosave("review")
            st.rerun()


# ------------------------------------------------------------------
# Step 3：AI 总结
# ------------------------------------------------------------------

def _step3_generate() -> None:
    render_chat_step3("review")


# ------------------------------------------------------------------
# Step 4：导出
# ------------------------------------------------------------------

def _step4_export() -> None:
    st.subheader("Step 4：导出 Word 文档")
    st.success("复盘报告已就绪！")

    with st.expander("预览报告内容", expanded=False):
        st.markdown(st.session_state.generated)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回编辑", use_container_width=True):
            st.session_state.step = 3
            st.session_state.locked = False
            st.session_state.pop("review_last_export", None)
            st.session_state.pop("review_export_bytes", None)
            autosave("review")
            st.rerun()
    with col2:
        if st.button("📄 导出 Word 文档", use_container_width=True, type="primary"):
            with st.spinner("正在生成 Word 文档..."):
                result = export_review(st.session_state.generated)
            if result.success:
                st.session_state.review_last_export = str(result.local_path)
                st.session_state.review_export_bytes = result.file_bytes
                if sid := st.session_state.get("session_id"):
                    update_export(sid, str(result.local_path))
                if tid := st.session_state.get("task_id"):
                    add_export(tid, str(result.local_path))
                autosave("review", complete=True)
                st.rerun()
            else:
                st.error(f"❌ 导出失败：{result.error_message}")

    # ── 持久显示导出结果 ─────────────────────────────────────────
    if export_str := st.session_state.get("review_last_export"):
        p = Path(export_str)
        st.success(f"✅ 已保存到：{p}")
        col_dl, col_open = st.columns([3, 1])
        with col_dl:
            dl_bytes = st.session_state.get("review_export_bytes")
            if not dl_bytes and p.exists():
                try:
                    dl_bytes = p.read_bytes()
                except Exception:
                    dl_bytes = None
            if dl_bytes:
                st.download_button(
                    label="⬇️ 点击下载",
                    data=dl_bytes,
                    file_name=p.name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="review_dl_btn",
                )
            else:
                st.warning("文件已移动或被删除，请重新点击「导出 Word 文档」。")
        with col_open:
            if st.button("📂 打开文件夹", key="review_open_folder", use_container_width=True):
                err = open_folder(p.parent)
                if err:
                    st.info(f"文件夹路径：{err}")

    st.divider()
    if st.button("🔄 重新开始（新复盘）", use_container_width=True):
        if tid := st.session_state.get("task_id"):
            abandon_task_if_incomplete(tid)
        for key in (
            "step", "data", "generated", "session_id", "task_id",
            "locked", "messages", "parsed_table", "_recovery_declined",
            "qa_round", "qa_done",
            "review_draft_edit",
            "review_step2_rows", "review_step2_editor", "review_step2_notice",
            "review_img_info", "review_img_fp",
            "image_parsed_rows",
            "review_last_export", "review_export_bytes",
        ):
            st.session_state.pop(key, None)
        st.rerun()
