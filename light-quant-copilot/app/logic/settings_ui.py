"""
AI 设置页面。

由 ui.py 在 module == "settings" 时调用 render_settings_page()。

布局：
  1. 当前状态 banner
  2. 第一步：选择服务商（可用 / 即将支持）
  3. 第二步：填写 API Key
  4. 高级选项（折叠）
  5. 操作按钮：测试连接 / 保存并应用 / 重置演示模式
  6. 页脚：配置文件路径
"""
from __future__ import annotations

import streamlit as st

from .settings import (
    load_settings,
    mask_key,
    save_settings,
    settings_path,
    translate_error,
)
from .providers import default_provider, reconfigure_from_settings


# ══════════════════════════════════════════════════════════════════
# Provider 定义（国产优先）
# ══════════════════════════════════════════════════════════════════

_PROVIDERS: list[dict] = [
    {
        "id": "deepseek",
        "label": "⚡ DeepSeek",
        "desc": "国内访问速度快，性价比高，推荐首选",
        "badge": "⭐ 推荐",
        "available": True,
        "key_field": "deepseek_api_key",
        "model_field": "deepseek_model",
        "model_default": "deepseek-chat",
        "get_key_url": "https://platform.deepseek.com",
        "get_key_text": "获取 Key：platform.deepseek.com",
    },
    {
        "id": "qwen",
        "label": "🔷 阿里百炼/千问",
        "desc": "阿里云百炼平台，千问系列模型",
        "badge": "⭐ 推荐",
        "available": True,
        "key_field": "qwen_api_key",
        "model_field": "qwen_model",
        "model_default": "qwen-plus",
        "get_key_url": "https://bailian.console.aliyun.com",
        "get_key_text": "获取 Key：百炼控制台 → API-KEY 管理",
    },
    {
        "id": "zhipu",
        "label": "🧠 智谱 GLM",
        "desc": "清华系大模型，长文推理能力强",
        "badge": "🔜 即将支持",
        "available": False,
        "key_field": "zhipu_api_key",
        "model_field": "zhipu_model",
        "model_default": "glm-4",
        "get_key_url": "https://open.bigmodel.cn",
        "get_key_text": "获取 Key：open.bigmodel.cn",
    },
    {
        "id": "kimi",
        "label": "🌙 Moonshot Kimi",
        "desc": "长文本处理能力突出",
        "badge": "🔜 即将支持",
        "available": False,
        "key_field": "kimi_api_key",
        "model_field": "kimi_model",
        "model_default": "moonshot-v1-8k",
        "get_key_url": "https://platform.moonshot.cn",
        "get_key_text": "获取 Key：platform.moonshot.cn",
    },
    {
        "id": "volcengine",
        "label": "🌋 火山方舟",
        "desc": "字节跳动，豆包大模型",
        "badge": "🔜 即将支持",
        "available": False,
        "key_field": "volcengine_api_key",
        "model_field": "volcengine_model",
        "model_default": "ep-xxxxxxxxxx",
        "get_key_url": "https://www.volcengine.com/product/ark",
        "get_key_text": "获取 Key：火山引擎控制台",
    },
]

_AVAILABLE_IDS = [p["id"] for p in _PROVIDERS if p["available"]]
_PROVIDER_MAP = {p["id"]: p for p in _PROVIDERS}


# ══════════════════════════════════════════════════════════════════
# 会话状态初始化（仅第一次进入设置页时执行）
# ══════════════════════════════════════════════════════════════════

def _init_settings_state() -> None:
    """从已保存配置初始化表单 session_state，仅执行一次。"""
    if st.session_state.get("_ssets_initialized"):
        return
    cfg = load_settings()
    # 选中的 provider（仅可用的）
    saved_provider = cfg.get("provider", "mock")
    st.session_state._ssets_provider = (
        saved_provider if saved_provider in _AVAILABLE_IDS else "deepseek"
    )
    st.session_state._ssets_ds_model = cfg.get("deepseek_model", "deepseek-chat")
    st.session_state._ssets_qw_model = cfg.get("qwen_model", "qwen-plus")
    st.session_state._ssets_gateway_url = cfg.get("gateway_base_url", "")
    st.session_state._ssets_temperature = float(cfg.get("temperature", 0.3))
    st.session_state._ssets_max_tokens = int(cfg.get("max_tokens", 2048))
    st.session_state._ssets_timeout = int(cfg.get("timeout", 60))
    st.session_state._ssets_test_result = None   # {ok, msg} or None
    st.session_state._ssets_cfg = cfg             # 缓存，避免子函数重复读磁盘
    st.session_state._ssets_initialized = True


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def render_settings_page() -> None:
    """渲染完整 AI 设置页。"""
    _init_settings_state()

    st.title("⚙️ AI 设置")
    st.caption("配置好 API Key 后，三个模块将使用真实 AI 分析；无 Key 时自动使用演示模式。")

    # ── 当前状态 banner ──────────────────────────────────────────
    _render_status_banner()

    st.divider()

    # ── 第一步：选择服务商 ───────────────────────────────────────
    _render_provider_section()

    st.divider()

    # ── 第二步：API Key ──────────────────────────────────────────
    _render_key_section()

    # ── 第三步（可选）：中转站 / 网关 Base URL ──────────────────
    _render_gateway_section()

    # ── 高级选项 ─────────────────────────────────────────────────
    with st.expander("⚙️ 高级选项（模型名称 / 生成参数）", expanded=False):
        _render_advanced_options()

    st.divider()

    # ── 操作按钮 ─────────────────────────────────────────────────
    _render_action_buttons()

    # ── 测试导出 ─────────────────────────────────────────────────
    with st.expander("🧪 测试 Word 导出", expanded=False):
        _render_test_export()

    # ── 页脚 ─────────────────────────────────────────────────────
    st.divider()
    st.caption(f"配置文件位置（仅限本机）：`{settings_path()}`")
    st.caption("⚠️ API Key 存储在本地文件中，请勿将此文件分享给他人。")


# ══════════════════════════════════════════════════════════════════
# 各分区渲染函数
# ══════════════════════════════════════════════════════════════════

def _render_status_banner() -> None:
    if default_provider.is_mock:
        st.info(
            "🎭 **当前为演示模式** — 分析结果为示例模板，非真实 AI 分析。"
            " 填写 API Key 并点击「保存并应用」后即可启用真实分析。"
        )
    else:
        mode = default_provider.mode_label
        st.success(f"✅ **已启用真实分析** — {mode}")


def _render_provider_section() -> None:
    st.subheader("第一步：选择 AI 服务商")

    # 可用服务商（单选）
    available = [p for p in _PROVIDERS if p["available"]]
    provider_ids = [p["id"] for p in available]
    provider_labels = {p["id"]: f"{p['label']}  {p['badge']}  — {p['desc']}"
                       for p in available}

    cur_idx = (provider_ids.index(st.session_state._ssets_provider)
               if st.session_state._ssets_provider in provider_ids else 0)

    selected = st.radio(
        "服务商选择",
        options=provider_ids,
        format_func=provider_labels.get,
        index=cur_idx,
        key="_ssets_provider",
        label_visibility="collapsed",
    )

    # 即将支持（展示用，不可选）
    coming = [p for p in _PROVIDERS if not p["available"]]
    if coming:
        labels = "  ·  ".join(f"{p['label']} {p['badge']}" for p in coming)
        st.caption(f"即将支持：{labels}")


def _render_key_section() -> None:
    st.subheader("第二步：填写 API Key")

    sel = st.session_state._ssets_provider
    provider_info = _PROVIDER_MAP.get(sel, _PROVIDERS[0])
    key_field = provider_info["key_field"]
    get_key_text = provider_info["get_key_text"]

    # 检查是否已有已保存的 Key（从 session_state 缓存读取，不重复读磁盘）
    saved_cfg = st.session_state.get("_ssets_cfg", {})
    existing_key = saved_cfg.get(key_field, "")
    if existing_key:
        masked = mask_key(existing_key)
        st.caption(f"当前已配置 Key：`{masked}`  ← 留空则保留现有配置")
        placeholder = "留空则保留现有 Key，填写新值则替换"
    else:
        placeholder = "sk-xxxxxxxxxxxxxxxx"

    key_input = st.text_input(
        f"{provider_info['label']} API Key",
        value="",
        type="password",
        placeholder=placeholder,
        key=f"_ssets_key_input_{sel}",
        help="Key 仅保存在本机，不上传到任何服务器",
    )
    st.caption(f"🔗 {get_key_text}")


def _render_gateway_section() -> None:
    """渲染可选的中转站 / 网关 Base URL 配置。"""
    st.subheader("第三步（可选）：中转站 / 网关 Base URL")
    st.caption(
        "如果需要通过代理或中转站访问 API，在此填写中转站地址；留空则直连官方接口。"
    )

    gateway_val = st.session_state.get("_ssets_gateway_url", "")
    gateway_input = st.text_input(
        "中转站接口地址（Base URL）",
        value=gateway_val,
        placeholder="https://your-gateway.example.com/v1",
        key="_ssets_gateway_url",
        help="示例：https://api.example.com/v1  ·  留空则直连官方接口  ·  仅需填写一次，对所有服务商生效",
    )

    if gateway_input.strip():
        from .llm.config import normalize_base_url
        normalized = normalize_base_url(gateway_input.strip())
        if not normalized:
            st.warning("⚠️ 地址格式有误：需以 http:// 或 https:// 开头")
        else:
            st.caption(f"✅ 实际使用地址：`{normalized}`")


def _render_advanced_options() -> None:
    sel = st.session_state._ssets_provider
    provider_info = _PROVIDER_MAP.get(sel, _PROVIDERS[0])

    # 模型名称
    model_field = provider_info["model_field"]
    model_default = provider_info["model_default"]
    saved_model = st.session_state.get("_ssets_cfg", {}).get(model_field, model_default)

    st.text_input(
        "模型名称",
        value=saved_model,
        key="_ssets_model_name",
        help="普通用户保持默认即可，不确定请勿修改",
        placeholder=model_default,
    )

    # 温度
    st.slider(
        "生成温度（越低越保守稳定，分析类推荐 0.2–0.4）",
        min_value=0.1,
        max_value=1.0,
        step=0.1,
        key="_ssets_temperature",
        help="控制 AI 回答的随机性。量化分析场景推荐 0.3",
    )

    # 最大输出长度
    st.selectbox(
        "最大输出长度（token 数）",
        options=[1024, 2048, 4096],
        index=[1024, 2048, 4096].index(
            min([1024, 2048, 4096],
                key=lambda x: abs(x - st.session_state._ssets_max_tokens))
        ),
        key="_ssets_max_tokens",
        help="建议保持 2048（约 1 页），太长可能增加费用",
    )

    # 超时
    st.number_input(
        "超时时长（秒）",
        min_value=10,
        max_value=120,
        step=10,
        key="_ssets_timeout",
        help="网络较慢时可适当调大",
    )

    st.caption("流式输出（逐字显示）功能开发中，当前不可用。")


def _render_action_buttons() -> None:
    col_test, col_save, col_reset = st.columns([1, 1, 1])

    sel = st.session_state._ssets_provider
    provider_info = _PROVIDER_MAP.get(sel, _PROVIDERS[0])
    key_field = provider_info["key_field"]
    model_field = provider_info["model_field"]
    model_default = provider_info["model_default"]

    # ── 读取当前表单值 ───────────────────────────────────────────
    def _build_new_settings() -> dict:
        """从表单 session_state 和已保存配置合并，生成新的 settings dict。"""
        saved = load_settings()
        # Key：输入框有值则替换，否则沿用已保存的
        new_key = st.session_state.get(f"_ssets_key_input_{sel}", "").strip()
        if new_key:
            saved[key_field] = new_key
        # 其他字段
        saved["provider"] = sel
        saved[model_field] = st.session_state.get("_ssets_model_name", model_default)
        saved["temperature"] = float(st.session_state.get("_ssets_temperature", 0.3))
        saved["max_tokens"] = int(st.session_state.get("_ssets_max_tokens", 2048))
        saved["timeout"] = int(st.session_state.get("_ssets_timeout", 60))
        # 网关 URL：存原始值，router 使用时规范化
        raw_gw = st.session_state.get("_ssets_gateway_url", "").strip()
        saved["gateway_base_url"] = raw_gw
        return saved

    # ── 测试连接 ─────────────────────────────────────────────────
    with col_test:
        if st.button("🔌 测试连接", use_container_width=True):
            test_settings = _build_new_settings()
            api_key = test_settings.get(key_field, "")
            if not api_key:
                st.session_state._ssets_test_result = {
                    "ok": False,
                    "msg": "请先填写 API Key 再测试连接",
                }
            else:
                with st.spinner("正在测试连接..."):
                    result = _do_test(test_settings)
                st.session_state._ssets_test_result = result
            st.rerun()

    # ── 保存并应用 ────────────────────────────────────────────────
    with col_save:
        if st.button("💾 保存并应用", use_container_width=True, type="primary"):
            new_settings = _build_new_settings()
            api_key = new_settings.get(key_field, "")
            if not api_key:
                st.session_state._ssets_test_result = {
                    "ok": False,
                    "msg": "请先填写 API Key 再保存",
                }
            else:
                ok = save_settings(new_settings)
                if ok:
                    reconfigure_from_settings(new_settings)
                    st.session_state._ssets_initialized = False   # 下次重新初始化
                    st.session_state._ssets_test_result = {
                        "ok": True,
                        "msg": f"设置已保存并生效。{default_provider.mode_label}",
                    }
                else:
                    st.session_state._ssets_test_result = {
                        "ok": False,
                        "msg": "配置文件写入失败，请检查磁盘权限",
                    }
            st.rerun()

    # ── 重置演示模式 ──────────────────────────────────────────────
    with col_reset:
        if st.button("🎭 重置演示模式", use_container_width=True):
            from .providers import MockProvider
            default_provider.set_backend(MockProvider())
            st.session_state._ssets_test_result = {
                "ok": True,
                "msg": "已切换回演示模式，API Key 未删除（保存在本地配置文件中）",
            }
            st.session_state._ssets_initialized = False
            st.rerun()

    # ── 显示操作结果 ──────────────────────────────────────────────
    result = st.session_state.get("_ssets_test_result")
    if result:
        if result["ok"]:
            st.success(result["msg"])
        else:
            st.error(result["msg"])


# ══════════════════════════════════════════════════════════════════
# 内部：实际连接测试
# ══════════════════════════════════════════════════════════════════

def _do_test(settings: dict) -> dict:
    """
    临时构建 provider 进行连通性测试。
    不影响 default_provider 的当前状态。

    Returns:
        {"ok": bool, "msg": str}
    """
    from .settings import settings_to_llm_config
    from .llm import rebuild_router
    from .providers import RealProvider

    try:
        cfg = settings_to_llm_config(settings)
        router = rebuild_router(cfg)
        if router.is_mock:
            return {"ok": False, "msg": "未检测到有效 API Key，请检查填写内容"}
        provider = RealProvider(router)
        r = provider.test_connection()
        if r["ok"]:
            latency = r.get("latency_ms", 0)
            model = router.active_model
            prov = router.active_provider.upper()
            snippet = r.get("text", "")[:20] if r.get("text") else ""
            snippet_str = f"  回复：{snippet}" if snippet else ""
            base_url = router.active_base_url
            via_str = f"  经由：`{base_url}`" if base_url else ""
            return {
                "ok": True,
                "msg": f"✅ 已连接，可用真实分析（{prov} / {model}，延迟 {latency}ms）{snippet_str}{via_str}",
            }
        else:
            return {"ok": False, "msg": translate_error(r.get("error", "未知错误"))}
    except Exception as e:
        return {"ok": False, "msg": translate_error(str(e))}


# ══════════════════════════════════════════════════════════════════
# 测试 Word 导出
# ══════════════════════════════════════════════════════════════════

def _render_test_export() -> None:
    """渲染「测试 Word 导出」区块，验证 python-docx + 下载 + 落盘是否正常。"""
    st.caption(
        "生成一份包含标题、段落、列表、表格的示例 docx，"
        "用于验证导出链路（与 AI 设置无关）。"
    )
    if st.button("生成 test_export.docx", key="_test_export_btn"):
        from .export import export_test_doc
        with st.spinner("正在生成测试文档..."):
            result = export_test_doc()
        st.session_state._test_export_result = result

    result = st.session_state.get("_test_export_result")
    if result is None:
        return

    if result.success:
        st.success(f"✅ 测试文档已生成并落盘：`{result.local_path}`")
        st.download_button(
            label="⬇️ 下载 test_export.docx",
            data=result.file_bytes,
            file_name="test_export.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="_test_export_dl",
        )
    else:
        st.error(f"❌ 测试导出失败：{result.error_message}")

