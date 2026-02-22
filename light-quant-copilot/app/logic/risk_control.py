"""
风控向导（4步 Wizard）。
Step1 填写持仓（支持截图上传）→ Step2 可编辑表格确认 → Step3 AI分析 → Step4 导出

图片处理策略（无需 OCR）：
  - 上传图片 → Pillow 预处理（缩放 + 压缩）→ 显示优化信息
  - 若 provider.supports_vision → 调用 parse_image() 自动识别持仓结构
  - 无论识别成功与否 → Step2 始终展示可编辑表格
  - 识别失败/无 vision → 表格用文本解析数据或空行填充，供手动补录
"""
from __future__ import annotations
from pathlib import Path
import math
import streamlit as st
import pandas as pd

from .providers import default_provider
from .export import export_risk_report, open_folder
from .db import update_export, add_export, abandon_task_if_incomplete
from .state_manager import maybe_offer_recovery, autosave
from .image_utils import preprocess_image
from .chat_ui import render_chat_step3


# ── 辅助：安全类型转换 ─────────────────────────────────────────────

def _sf(v, default: float = 0.0) -> float:
    """安全转 float，处理 None / NaN。"""
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

def render_risk_wizard() -> None:
    st.title("🛡️ 风控向导")

    if maybe_offer_recovery("risk"):
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
    labels = ["1 填写持仓", "2 核对数据", "3 AI 分析", "4 导出"]
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
# 辅助：解析持仓文本
# ------------------------------------------------------------------

def _parse_positions(text: str) -> list[dict]:
    result = []
    if not text.strip():
        return result
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.replace("，", ",").split(",")]
        if len(parts) < 1 or not parts[0]:
            continue
        try:
            result.append({
                "stock": parts[0],
                "cost": float(parts[1]) if len(parts) > 1 and parts[1] else 0.0,
                "current": float(parts[2]) if len(parts) > 2 and parts[2] else 0.0,
                "qty": int(parts[3]) if len(parts) > 3 and parts[3] else 0,
            })
        except ValueError:
            pass
    return result


# ------------------------------------------------------------------
# Step 1：填写持仓（+ 可选截图上传）
# ------------------------------------------------------------------

def _step1_input() -> None:
    st.subheader("Step 1：填写持仓与风控参数")

    # ── 截图上传区（表单外，上传后立即预处理）───────────────────────
    with st.expander("📸 上传持仓截图自动识别（可选，无需 OCR）", expanded=False):
        uploaded = st.file_uploader(
            "支持 JPG / PNG / BMP / WebP",
            type=["jpg", "jpeg", "png", "bmp", "webp"],
            key="risk_img_upload",
        )
        if uploaded:
            fp = f"{uploaded.name}_{uploaded.size}"
            if st.session_state.get("risk_img_fp") != fp:
                with st.spinner("正在优化图片..."):
                    info = preprocess_image(uploaded)
                st.session_state.risk_img_info = info
                st.session_state.risk_img_fp = fp

            info = st.session_state.risk_img_info
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
                    st.info("Vision AI 将在下一步自动识别持仓数据")
                else:
                    st.caption("当前 Provider 无 Vision 能力，图片已优化缓存；请在 Step2 手动核对数据。")

    prev = st.session_state.get("data", {})

    # ── 文字录入表单 ──────────────────────────────────────────────
    with st.form("risk_step1"):
        col1, col2 = st.columns(2)
        with col1:
            max_drawdown = st.number_input(
                "组合最大回撤限制（%）",
                min_value=1.0, max_value=50.0,
                value=float(prev.get("max_drawdown", 10.0)),
                step=0.5,
            )
        with col2:
            stop_loss = st.number_input(
                "个股止损线（%）",
                min_value=1.0, max_value=30.0,
                value=float(prev.get("stop_loss", 5.0)),
                step=0.5,
            )

        st.markdown("---")
        st.markdown("**持仓录入**（每行一只，格式：代码,成本价,现价,持股数）")
        st.caption("若已上传截图，此处可留空；有图有文时以文本为准")
        positions_text = st.text_area(
            "持仓数据",
            value=prev.get("positions_raw", ""),
            placeholder="600519,1800,1850,100\n000001,12.50,13.20,500",
            height=140,
        )
        submitted = st.form_submit_button("下一步 →", use_container_width=True, type="primary")

    if submitted:
        positions = _parse_positions(positions_text)
        st.session_state.data = {
            "max_drawdown": max_drawdown,
            "stop_loss": stop_loss,
            "positions": positions,
            "positions_raw": positions_text,
        }
        st.session_state.step = 2
        st.session_state.generated = ""
        st.session_state.locked = False

        # ── 图片解析（有图才调用）────────────────────────────────
        img_info = st.session_state.get("risk_img_info")
        if img_info:
            with st.spinner("正在识别截图内容..."):
                parsed = default_provider.parse_image("risk", img_info["bytes"])
            st.session_state.image_parsed_rows = parsed or []
        else:
            st.session_state.pop("image_parsed_rows", None)

        # 清空 Step2 缓存，强制重新初始化
        st.session_state.pop("risk_step2_rows", None)
        st.session_state.pop("risk_step2_editor", None)
        st.rerun()


# ------------------------------------------------------------------
# Step 2：可编辑持仓表格 + 实时风险预览
# ------------------------------------------------------------------

def _step2_confirm() -> None:
    st.subheader("Step 2：核对持仓数据")

    data = st.session_state.data
    stop_loss = float(data["stop_loss"])

    st.markdown(
        f"**风控参数**：最大回撤 `{data['max_drawdown']}%` ｜ 个股止损线 `{stop_loss}%`"
    )

    # ── 初始化可编辑行（每次进入 Step2 只执行一次）──────────────
    if "risk_step2_rows" not in st.session_state:
        if pt := st.session_state.get("parsed_table"):
            # 从存档恢复
            rows = pt
            st.session_state.risk_step2_notice = "💾 已从存档恢复持仓数据，请核对后确认。"
        else:
            image_rows: list[dict] = st.session_state.pop("image_parsed_rows", None) or []
            if image_rows:
                rows = [
                    {
                        "股票代码": r.get("stock", ""),
                        "成本价": float(r.get("cost", 0) or 0),
                        "现价": float(r.get("current", 0) or 0),
                        "持股数": int(float(r.get("qty", 0) or 0)),
                    }
                    for r in image_rows
                ]
                cnt = len([r for r in rows if r["股票代码"]])
                st.session_state.risk_step2_notice = (
                    f"✅ 已从截图识别 {cnt} 条持仓数据，请核对后确认。"
                )
            else:
                positions = data.get("positions", [])
                rows = [
                    {
                        "股票代码": p["stock"],
                        "成本价": float(p["cost"]),
                        "现价": float(p["current"]),
                        "持股数": int(p["qty"]),
                    }
                    for p in positions
                ]
                if not rows:
                    st.session_state.risk_step2_notice = (
                        "💡 未录入持仓，请在下方表格中手动添加，或返回上传截图。"
                    )
                else:
                    st.session_state.pop("risk_step2_notice", None)
        if not rows:
            rows = [{"股票代码": "", "成本价": 0.0, "现价": 0.0, "持股数": 0}]
        st.session_state.risk_step2_rows = rows

    # ── 显示来源提示 ──────────────────────────────────────────────
    if notice := st.session_state.get("risk_step2_notice"):
        st.info(notice)

    # ── 可编辑持仓表格 ────────────────────────────────────────────
    st.markdown("**持仓列表**（可直接编辑、添加或删除行）")
    initial_df = pd.DataFrame(st.session_state.risk_step2_rows)

    edited_df = st.data_editor(
        initial_df,
        num_rows="dynamic",
        width="stretch",
        key="risk_step2_editor",
        column_config={
            "股票代码": st.column_config.TextColumn(
                "股票代码", help="股票代码（如 600519）", required=True, max_chars=10
            ),
            "成本价": st.column_config.NumberColumn(
                "成本价", format="%.2f", min_value=0.0, step=0.01
            ),
            "现价": st.column_config.NumberColumn(
                "现价", format="%.2f", min_value=0.0, step=0.01
            ),
            "持股数": st.column_config.NumberColumn(
                "持股数", format="%d", min_value=0, step=1
            ),
        },
    )

    # ── 实时风险预览（基于当前编辑内容自动计算）─────────────────
    risk_rows = []
    for _, row in edited_df.iterrows():
        code = str(row.get("股票代码") or "").strip()
        if not code:
            continue
        cost = _sf(row.get("成本价"))
        current = _sf(row.get("现价"))
        qty = _si(row.get("持股数"))
        pnl_pct = ((current - cost) / cost * 100) if cost > 0 else 0.0
        pnl_amt = (current - cost) * qty
        rating = (
            "🔴 警戒" if pnl_pct < -stop_loss
            else ("🟡 关注" if pnl_pct < 0 else "🟢 正常")
        )
        risk_rows.append({
            "股票": code,
            "盈亏%": f"{pnl_pct:+.1f}%",
            "盈亏金额": f"{pnl_amt:+,.0f}",
            "风险": rating,
        })

    if risk_rows:
        with st.expander("📊 风险预览（实时）", expanded=True):
            st.dataframe(pd.DataFrame(risk_rows), width="stretch", hide_index=True)

    # ── 去重 + 导航 ───────────────────────────────────────────────
    col_dedup, _ = st.columns([1, 4])
    with col_dedup:
        if st.button("一键去重", key="risk_dedup"):
            deduped = edited_df.drop_duplicates(subset=["股票代码"]).reset_index(drop=True)
            st.session_state.risk_step2_rows = deduped.to_dict("records")
            st.session_state.pop("risk_step2_editor", None)
            st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回修改", use_container_width=True):
            st.session_state.pop("risk_step2_rows", None)
            st.session_state.pop("risk_step2_editor", None)
            st.session_state.pop("risk_step2_notice", None)
            st.session_state.step = 1
            st.rerun()
    with col2:
        if st.button("确认，生成风控报告 →", use_container_width=True, type="primary"):
            # 将编辑后的表格写回 data["positions"]
            positions = []
            for _, row in edited_df.iterrows():
                code = str(row.get("股票代码") or "").strip()
                if not code:
                    continue
                positions.append({
                    "stock": code,
                    "cost": _sf(row.get("成本价")),
                    "current": _sf(row.get("现价")),
                    "qty": _si(row.get("持股数")),
                })
            st.session_state.data["positions"] = positions
            # parsed_table 存储可编辑行格式（供恢复 Step2 使用）
            st.session_state.parsed_table = edited_df.to_dict("records")
            st.session_state.pop("risk_step2_rows", None)
            st.session_state.pop("risk_step2_editor", None)
            st.session_state.pop("risk_step2_notice", None)
            st.session_state.step = 3
            autosave("risk")
            st.rerun()


# ------------------------------------------------------------------
# Step 3：AI 分析
# ------------------------------------------------------------------

def _step3_generate() -> None:
    render_chat_step3("risk")


# ------------------------------------------------------------------
# Step 4：导出
# ------------------------------------------------------------------

def _step4_export() -> None:
    st.subheader("Step 4：导出 Word 文档")
    st.success("风控报告已就绪！")

    with st.expander("预览报告内容", expanded=False):
        st.markdown(st.session_state.generated)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回编辑", use_container_width=True):
            st.session_state.step = 3
            st.session_state.locked = False
            st.session_state.pop("risk_last_export", None)
            st.session_state.pop("risk_export_bytes", None)
            autosave("risk")
            st.rerun()
    with col2:
        if st.button("📄 导出 Word 文档", use_container_width=True, type="primary"):
            with st.spinner("正在生成 Word 文档..."):
                result = export_risk_report(st.session_state.generated)
            if result.success:
                st.session_state.risk_last_export = str(result.local_path)
                st.session_state.risk_export_bytes = result.file_bytes
                if sid := st.session_state.get("session_id"):
                    update_export(sid, str(result.local_path))
                if tid := st.session_state.get("task_id"):
                    add_export(tid, str(result.local_path))
                autosave("risk", complete=True)
                st.rerun()
            else:
                st.error(f"❌ 导出失败：{result.error_message}")

    # ── 持久显示导出结果 ─────────────────────────────────────────
    if export_str := st.session_state.get("risk_last_export"):
        p = Path(export_str)
        st.success(f"✅ 已保存到：{p}")
        col_dl, col_open = st.columns([3, 1])
        with col_dl:
            dl_bytes = st.session_state.get("risk_export_bytes")
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
                    key="risk_dl_btn",
                )
            else:
                st.warning("文件已移动或被删除，请重新点击「导出 Word 文档」。")
        with col_open:
            if st.button("📂 打开文件夹", key="risk_open_folder", use_container_width=True):
                err = open_folder(p.parent)
                if err:
                    st.info(f"文件夹路径：{err}")

    st.divider()
    if st.button("🔄 重新开始（新风控检查）", use_container_width=True):
        if tid := st.session_state.get("task_id"):
            abandon_task_if_incomplete(tid)
        for key in (
            "step", "data", "generated", "session_id", "task_id",
            "locked", "messages", "parsed_table", "_recovery_declined",
            "qa_round", "qa_done",
            "risk_draft_edit",
            "risk_step2_rows", "risk_step2_editor", "risk_step2_notice",
            "risk_img_info", "risk_img_fp",
            "image_parsed_rows",
            "risk_last_export", "risk_export_bytes",
        ):
            st.session_state.pop(key, None)
        st.rerun()
