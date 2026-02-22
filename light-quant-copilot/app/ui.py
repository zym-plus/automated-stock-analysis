"""
light-quant-copilot — 主入口
启动：streamlit run app/ui.py
"""
from __future__ import annotations
import sys
from pathlib import Path

# 确保项目根目录在 sys.path，支持绝对/相对两种启动方式
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 加载 .env 文件（若存在）；python-dotenv 未安装时静默跳过
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=False)
except ImportError:
    pass

import streamlit as st

from app.logic.db import init_db
from app.logic.morning_report import render_morning_wizard
from app.logic.risk_control import render_risk_wizard
from app.logic.review import render_review_wizard
from app.logic.state_manager import autosave, clear_module_state, sync_draft_to_generated
from app.logic.providers import default_provider, reconfigure_from_settings
from app.logic.settings_ui import render_settings_page


# ------------------------------------------------------------------
# 内部工具：模块崩溃时的降级展示
# ------------------------------------------------------------------

def _show_module_error(module_label: str, exc: Exception) -> None:
    """当某模块崩溃时，显示友好错误页（不影响其他模块）。"""
    import traceback
    st.error(f"🔴 **{module_label}** 模块出现异常，已自动隔离，不影响其他功能。")
    if st.session_state.get("_debug_mode"):
        st.code(traceback.format_exc(), language="python")
    else:
        st.caption(f"错误摘要：{type(exc).__name__}: {str(exc)[:150]}")
        st.caption("若反复出现，请开启左侧「🐛 调试模式」查看详情，或点击下方按钮重置该模块。")
    col_back, col_reset = st.columns(2)
    with col_back:
        if st.button("↩ 返回首页", key=f"_err_back_{module_label}"):
            st.session_state.module = None
            st.rerun()
    with col_reset:
        if st.button("🔄 重置该模块", key=f"_err_reset_{module_label}"):
            from app.logic.state_manager import clear_module_state
            clear_module_state()
            st.rerun()


# ------------------------------------------------------------------
# 页面配置
# ------------------------------------------------------------------

st.set_page_config(
    page_title="量化副驾 · Light",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ------------------------------------------------------------------
# 初始化
# ------------------------------------------------------------------

# 数据库初始化（失败时降级：功能不可用但页面仍能打开）
_DB_OK = False
try:
    init_db()
    _DB_OK = True
except Exception as _db_init_err:
    pass   # 在侧边栏渲染完成后提示，这里先静默跳过

_SS_DEFAULTS: dict = {
    "module": None,
    "step": 1,
    "data": {},
    "generated": "",
    "messages": [],
    "locked": False,
    "parsed_table": None,
}
for _k, _v in _SS_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── 启动时从本地配置文件加载 AI 设置（仅执行一次）─────────────────
if not st.session_state.get("_provider_loaded_from_settings"):
    st.session_state._provider_loaded_from_settings = True
    from app.logic.settings import load_settings, has_saved_config
    if has_saved_config():
        _saved_cfg = load_settings()
        if _saved_cfg.get("provider", "mock") != "mock":
            try:
                reconfigure_from_settings(_saved_cfg)
            except Exception:
                pass   # 加载失败静默回退 Mock，不影响启动


# ------------------------------------------------------------------
# 辅助：切换模块
# ------------------------------------------------------------------

def _switch_module(name: str) -> None:
    if st.session_state.module == name:
        return

    current = st.session_state.module

    # 切换前：把 Step3 textarea 的最新编辑同步到 generated，再持久化
    if current and current != "settings" and st.session_state.get("task_id"):
        if st.session_state.get("step") == 3:
            sync_draft_to_generated(current)
        autosave(current)

    # 清空向导 session_state，进入新模块
    clear_module_state()
    st.session_state.module = name


# ------------------------------------------------------------------
# 侧边栏导航
# ------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 📈 量化副驾")
    st.caption("本地运行 · 数据不出本机")
    st.divider()

    if st.button("🌅  晨　报", use_container_width=True):
        _switch_module("morning")
        st.rerun()

    if st.button("🛡️  风　控", use_container_width=True):
        _switch_module("risk")
        st.rerun()

    if st.button("📊  复　盘", use_container_width=True):
        _switch_module("review")
        st.rerun()

    st.divider()

    if st.button("⚙️  AI 设置", use_container_width=True):
        _switch_module("settings")
        st.rerun()

    # ── 当前 AI 模式标签 ─────────────────────────────────────────
    st.caption(default_provider.mode_label)
    st.caption("v0.2.0")

    # ── 调试模式开关（仅本地）────────────────────────────────────
    st.divider()
    st.toggle("🐛 调试模式", value=False, key="_debug_mode")

    # ── DB 状态警告 ───────────────────────────────────────────────
    if not _DB_OK:
        st.warning("⚠️ 数据库初始化失败，历史记录和自动恢复功能暂不可用。")


# ------------------------------------------------------------------
# 演示模式横幅（在模块页顶部显示）
# ------------------------------------------------------------------

def _maybe_show_mock_banner() -> None:
    """当 provider 为 Mock 时，在主内容区顶部显示提示条。"""
    if default_provider.is_mock:
        st.info(
            "🎭 **当前为演示模式** — 分析结果为示例模板，非真实 AI 分析。"
            " 点击左侧 **⚙️ AI 设置** 配置 API Key 后可启用真实分析。"
        )


# ------------------------------------------------------------------
# 主内容区
# ------------------------------------------------------------------

if st.session_state.module is None:
    # 欢迎首页
    st.title("欢迎使用量化副驾 📈")
    st.markdown(
        f"""
        这是一款专为**普通股民**设计的本地 AI 助手，全程向导式操作，无需任何编程知识。

        ---

        ### 三大功能

        | 功能 | 说明 | 导出文件 |
        |------|------|---------||
        | 🌅 **晨报** | 开盘前生成个股分析简报 | `YYYY-MM-DD_晨报.docx` |
        | 🛡️ **风控** | 持仓风险检查，自动标记止损警戒 | `风控清单.docx` |
        | 📊 **复盘** | 收盘后总结交易，记录心得 | `收盘复盘.docx` |

        ### 使用流程（每个模块均为 4 步）

        1. **填写信息** — 输入股票、持仓或交易数据
        2. **确认数据** — 检查表格，确认无误
        3. **AI 生成** — 一键生成报告，可自由编辑
        4. **导出文档** — 保存为 Word，支持直接下载

        ---

        > 当前使用 **{default_provider.mode_label}**。
        > 接入 DeepSeek / 千问后可获得个性化真实分析（点击左侧 ⚙️ AI 设置）。

        **← 点击左侧菜单开始使用**
        """
    )

elif st.session_state.module == "morning":
    _maybe_show_mock_banner()
    try:
        render_morning_wizard()
    except Exception as _e:
        _show_module_error("晨报", _e)

elif st.session_state.module == "risk":
    _maybe_show_mock_banner()
    try:
        render_risk_wizard()
    except Exception as _e:
        _show_module_error("风控", _e)

elif st.session_state.module == "review":
    _maybe_show_mock_banner()
    try:
        render_review_wizard()
    except Exception as _e:
        _show_module_error("复盘", _e)

elif st.session_state.module == "settings":
    try:
        render_settings_page()
    except Exception as _e:
        _show_module_error("AI 设置", _e)
