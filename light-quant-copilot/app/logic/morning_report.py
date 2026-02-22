"""
晨报向导（4步 Wizard）。
Step1 填写信息（支持截图上传）→ Step2 可编辑股票列表 → Step3 AI生成 → Step4 导出

图片处理策略（无需 OCR）：
  - 上传图片 → Pillow 预处理（缩放 + 压缩）→ 显示优化信息
  - 若 provider.supports_vision → 调用 parse_image() 识别股票列表
  - 无论识别成功与否 → Step2 始终展示可编辑表格
  - 识别失败/无 vision → 从文本输入初始化表格，供手动录入
"""
from __future__ import annotations
from pathlib import Path
import streamlit as st
import pandas as pd

from .providers import default_provider
from .export import export_morning_report, open_folder
from .db import update_export, add_export, abandon_task_if_incomplete
from .state_manager import maybe_offer_recovery, autosave
from .image_utils import preprocess_image
from .chat_ui import render_chat_step3


# ------------------------------------------------------------------
# 公开入口
# ------------------------------------------------------------------

def render_morning_wizard() -> None:
    st.title("🌅 晨报生成向导")

    if maybe_offer_recovery("morning"):
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
    labels = ["1 填写信息", "2 核对股票", "3 AI 生成", "4 导出"]
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
# Step 1：填写信息（+ 可选截图上传）
# ------------------------------------------------------------------

def _step1_input() -> None:
    st.subheader("Step 1：填写今日关注信息")

    # ── 截图上传区 ────────────────────────────────────────────────
    with st.expander("📸 上传自选股截图自动识别（可选，无需 OCR）", expanded=False):
        uploaded = st.file_uploader(
            "支持 JPG / PNG / BMP / WebP",
            type=["jpg", "jpeg", "png", "bmp", "webp"],
            key="morning_img_upload",
        )
        if uploaded:
            fp = f"{uploaded.name}_{uploaded.size}"
            if st.session_state.get("morning_img_fp") != fp:
                with st.spinner("正在优化图片..."):
                    info = preprocess_image(uploaded)
                st.session_state.morning_img_info = info
                st.session_state.morning_img_fp = fp

            info = st.session_state.morning_img_info
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
                    st.info("Vision AI 将在下一步自动识别股票列表")
                else:
                    st.caption("当前 Provider 无 Vision 能力，图片已优化缓存；请在 Step2 手动核对数据。")

    prev = st.session_state.get("data", {})

    # ── 文字录入表单 ──────────────────────────────────────────────
    with st.form("morning_step1"):
        stocks = st.text_input(
            "关注股票代码（逗号分隔，例：600519, 000001）",
            value=prev.get("stocks", ""),
            placeholder="600519, 000001, 300750",
        )
        sector = st.text_input(
            "关注板块（例：新能源、半导体、银行）",
            value=prev.get("sector", ""),
            placeholder="新能源, 半导体",
        )
        sentiment = st.selectbox(
            "今日市场情绪判断",
            ["偏多", "中性", "偏空", "不确定"],
            index=["偏多", "中性", "偏空", "不确定"].index(
                prev.get("sentiment", "中性")
            ),
        )
        notes = st.text_area(
            "今日特别关注（可选，如：美联储议息、重要财报等）",
            value=prev.get("notes", ""),
            height=80,
        )
        news = st.text_area(
            "今日公告 / 新闻摘要（可选，粘贴重要公告或新闻原文）",
            value=prev.get("news", ""),
            height=80,
            placeholder="如：某股发布业绩预增公告；美联储释放降息信号…",
        )
        submitted = st.form_submit_button("下一步 →", use_container_width=True, type="primary")

    if submitted:
        has_image = bool(st.session_state.get("morning_img_info"))
        if not stocks.strip() and not sector.strip() and not has_image:
            st.warning("请至少填写股票代码、关注板块，或上传截图。")
            return

        st.session_state.data = {
            "stocks": stocks.strip(),
            "sector": sector.strip(),
            "sentiment": sentiment,
            "notes": notes.strip(),
            "news": news.strip(),
        }
        st.session_state.step = 2
        st.session_state.generated = ""
        st.session_state.locked = False

        # ── 图片解析 ──────────────────────────────────────────────
        img_info = st.session_state.get("morning_img_info")
        if img_info:
            with st.spinner("正在识别截图内容..."):
                parsed = default_provider.parse_image("morning", img_info["bytes"])
            st.session_state.image_parsed_rows = parsed or []
        else:
            st.session_state.pop("image_parsed_rows", None)

        st.session_state.pop("morning_step2_rows", None)
        st.session_state.pop("morning_step2_editor", None)
        st.rerun()


# ------------------------------------------------------------------
# Step 2：可编辑股票列表 + 其他参数确认
# ------------------------------------------------------------------

def _step2_confirm() -> None:
    st.subheader("Step 2：核对今日关注股票")

    data = st.session_state.data

    # ── 初始化可编辑行 ────────────────────────────────────────────
    if "morning_step2_rows" not in st.session_state:
        if pt := st.session_state.get("parsed_table"):
            rows = pt
            st.session_state.morning_step2_notice = "💾 已从存档恢复股票列表，请核对后确认。"
        else:
            image_rows: list[dict] = st.session_state.pop("image_parsed_rows", None) or []
            if image_rows:
                rows = [
                    {
                        "股票代码": r.get("stock_code", ""),
                        "备注（可选）": r.get("note", ""),
                    }
                    for r in image_rows
                ]
                cnt = len([r for r in rows if r["股票代码"]])
                st.session_state.morning_step2_notice = (
                    f"✅ 已从截图识别 {cnt} 只股票，请核对后确认。"
                )
            else:
                stocks_text = data.get("stocks", "")
                codes = [
                    s.strip()
                    for s in stocks_text.replace("，", ",").split(",")
                    if s.strip()
                ]
                rows = [{"股票代码": c, "备注（可选）": ""} for c in codes]
                st.session_state.pop("morning_step2_notice", None)
        if not rows:
            rows = [{"股票代码": "", "备注（可选）": ""}]
        st.session_state.morning_step2_rows = rows

    if notice := st.session_state.get("morning_step2_notice"):
        st.info(notice)

    # ── 其他参数（只读展示）──────────────────────────────────────
    cols = st.columns(3)
    cols[0].metric("关注板块", data.get("sector") or "（未填写）")
    cols[1].metric("市场情绪", data.get("sentiment", ""))
    notes_preview = (data.get("notes") or "（无）")[:40]
    cols[2].metric("特别关注", notes_preview)
    st.markdown("---")

    # ── 可编辑股票表格 ────────────────────────────────────────────
    st.markdown("**关注股票列表**（可直接编辑、添加或删除行）")
    initial_df = pd.DataFrame(st.session_state.morning_step2_rows)

    edited_df = st.data_editor(
        initial_df,
        num_rows="dynamic",
        width="stretch",
        key="morning_step2_editor",
        column_config={
            "股票代码": st.column_config.TextColumn(
                "股票代码", help="如 600519", required=True, max_chars=10
            ),
            "备注（可选）": st.column_config.TextColumn(
                "备注（可选）", help="如：茅台、白酒龙头", max_chars=50
            ),
        },
    )

    # ── 去重 ──────────────────────────────────────────────────────
    col_dedup, _ = st.columns([1, 4])
    with col_dedup:
        if st.button("一键去重", key="morning_dedup"):
            deduped = edited_df.drop_duplicates(subset=["股票代码"]).reset_index(drop=True)
            st.session_state.morning_step2_rows = deduped.to_dict("records")
            st.session_state.pop("morning_step2_editor", None)
            st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回修改", use_container_width=True):
            st.session_state.pop("morning_step2_rows", None)
            st.session_state.pop("morning_step2_editor", None)
            st.session_state.pop("morning_step2_notice", None)
            st.session_state.step = 1
            st.rerun()
    with col2:
        if st.button("确认，生成报告 →", use_container_width=True, type="primary"):
            valid_codes = [
                str(row.get("股票代码") or "").strip()
                for _, row in edited_df.iterrows()
                if str(row.get("股票代码") or "").strip()
            ]
            st.session_state.data["stocks"] = ", ".join(valid_codes)
            st.session_state.parsed_table = edited_df.to_dict("records")
            st.session_state.pop("morning_step2_rows", None)
            st.session_state.pop("morning_step2_editor", None)
            st.session_state.pop("morning_step2_notice", None)
            st.session_state.step = 3
            autosave("morning")
            st.rerun()


# ------------------------------------------------------------------
# Step 3：AI 生成
# ------------------------------------------------------------------

def _step3_generate() -> None:
    render_chat_step3("morning")


# ------------------------------------------------------------------
# Step 4：导出
# ------------------------------------------------------------------

def _step4_export() -> None:
    st.subheader("Step 4：导出 Word 文档")
    st.success("报告已就绪！点击下方按钮导出。")

    with st.expander("预览报告内容", expanded=False):
        st.markdown(st.session_state.generated)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回编辑", use_container_width=True):
            st.session_state.step = 3
            st.session_state.locked = False
            st.session_state.pop("morning_last_export", None)
            st.session_state.pop("morning_export_bytes", None)
            autosave("morning")
            st.rerun()
    with col2:
        if st.button("📄 导出 Word 文档", use_container_width=True, type="primary"):
            with st.spinner("正在生成 Word 文档..."):
                result = export_morning_report(st.session_state.generated)
            if result.success:
                st.session_state.morning_last_export = str(result.local_path)
                st.session_state.morning_export_bytes = result.file_bytes
                if sid := st.session_state.get("session_id"):
                    update_export(sid, str(result.local_path))
                if tid := st.session_state.get("task_id"):
                    add_export(tid, str(result.local_path))
                autosave("morning", complete=True)
                st.rerun()
            else:
                st.error(f"❌ 导出失败：{result.error_message}")

    # ── 持久显示导出结果 ─────────────────────────────────────────
    if export_str := st.session_state.get("morning_last_export"):
        p = Path(export_str)
        st.success(f"✅ 已保存到：{p}")
        col_dl, col_open = st.columns([3, 1])
        with col_dl:
            # 优先用 session_state 中预生成的 bytes，避免重复读磁盘
            dl_bytes = st.session_state.get("morning_export_bytes")
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
                    key="morning_dl_btn",
                )
            else:
                st.warning("文件已移动或被删除，请重新点击「导出 Word 文档」。")
        with col_open:
            if st.button("📂 打开文件夹", key="morning_open_folder", use_container_width=True):
                err = open_folder(p.parent)
                if err:
                    st.info(f"文件夹路径：{err}")

    st.divider()
    if st.button("🔄 重新开始（生成新晨报）", use_container_width=True):
        if tid := st.session_state.get("task_id"):
            abandon_task_if_incomplete(tid)
        for key in (
            "step", "data", "generated", "session_id", "task_id",
            "locked", "messages", "parsed_table", "_recovery_declined",
            "qa_round", "qa_done",
            "morning_draft_edit",
            "morning_step2_rows", "morning_step2_editor", "morning_step2_notice",
            "morning_img_info", "morning_img_fp",
            "image_parsed_rows",
            "morning_last_export", "morning_export_bytes",
        ):
            st.session_state.pop(key, None)
        st.rerun()
