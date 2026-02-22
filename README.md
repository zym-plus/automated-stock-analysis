# Automated Stock Analysis
# 量化副驾 · Light Quant Copilot

> 面向普通股民的**本地 AI 量化助手**——零配置、全向导、四步生成专业报告，数据永不离机。

---

## 目录

- [快速开始](#快速开始)
- [三大功能模块](#三大功能模块)
- [四步向导流程](#四步向导流程)
- [截图识别功能](#截图识别功能)
- [接入真实 AI](#接入真实-ai)
- [中转站 / 网关配置](#中转站--网关配置)
- [数据与隐私](#数据与隐私)
- [项目结构](#项目结构)
- [环境变量参考](#环境变量参考)
- [版本历史](#版本历史)

---

## 快速开始

**无需任何命令行知识，双击即可运行。**

| 系统 | 操作 |
|------|------|
| **Windows** | 双击项目根目录中的 `run.bat` |
| **Linux / macOS / WSL** | 终端运行 `bash run.sh` |

脚本自动完成以下步骤：

1. 检测 Python 版本（需要 **3.11+**）
2. 创建隔离虚拟环境（`.venv/`）
3. 安装所有依赖
4. 初始化本地数据库
5. 自动打开浏览器

> **没有 Python？** 前往 [python.org/downloads](https://www.python.org/downloads/) 下载。
> Windows 安装时务必勾选 **"Add Python to PATH"**。

### 手动启动（开发者）

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
streamlit run app/ui.py
```

浏览器访问 `http://localhost:8501`

---

## 三大功能模块

| 模块 | 使用场景 | 导出文件 |
|------|---------|---------|
| 🌅 **晨报** | 开盘前，生成个股分析简报 + 今日 3 大风险 + 可执行动作清单 | `YYYY-MM-DD_晨报.docx` |
| 🛡️ **风控** | 盘中或收盘后，检查持仓风险、自动标记止损警戒线 | `YYYY-MM-DD_风控清单.docx` |
| 📊 **复盘** | 收盘后，记录交易、总结心得、生成可存档的复盘报告 | `YYYY-MM-DD_收盘复盘.docx` |

所有报告均以 **Word 文档**形式输出，按日期自动归档到 `我的报告/YYYY-MM-DD/` 目录，同时支持浏览器直接下载。

---

## 四步向导流程

每个模块均采用统一的四步向导，无需填表经验：

```
第 1 步  填写信息        输入股票代码、持仓数据或交易记录
                         ↕  可选：上传截图自动识别
第 2 步  核对数据        在可编辑表格中确认 / 修改 / 去重
                         ↕  实时展示盈亏预览或成交汇总
第 3 步  AI 生成         AI 生成初稿，支持多轮对话补充
                         ↕  草稿可直接在页面上编辑
第 4 步  导出文档        一键生成 Word，自动落盘 + 浏览器下载
```

### 自动暂存 & 会话恢复

- 完成第 2 步确认后，向导状态自动保存到本地 SQLite
- 意外关闭或刷新后，重新进入同一模块会弹出"继续上次任务"横幅
- 可随时选择继续或放弃，不会丢失数据

---

## 截图识别功能

三个模块均支持上传持仓或成交截图，**无需 OCR / Tesseract**：

- **支持格式**：JPG / PNG / BMP / WebP
- **自动处理**：等比缩放（长边 ≤ 1500px）+ JPEG 压缩，优化后缓存到 `data/images_cache/`
- **识别路径**：若当前 AI Provider 支持 Vision，自动调用识别并预填表格；否则跳过识别，直接进入手动编辑表格

> 无论识别是否成功，第 2 步始终展示可编辑表格，确保数据可以手动核对。

---

## 接入真实 AI

默认使用内置 **MockProvider（演示模式）**，无需任何配置即可完整走通四步流程并导出 Word。

接入真实 AI 后，第 3 步将由 AI 根据您的实际数据生成个性化报告。

### 支持的服务商

| 服务商 | 状态 | 推荐理由 |
|--------|------|---------|
| ⚡ **DeepSeek** | ✅ 可用 | 国内速度快，性价比高，首选 |
| 🔷 **阿里百炼 / 千问** | ✅ 可用 | 阿里云百炼平台，千问系列模型 |
| 🧠 智谱 GLM | 🔜 即将支持 | — |
| 🌙 Moonshot Kimi | 🔜 即将支持 | — |
| 🌋 火山方舟（豆包） | 🔜 即将支持 | — |

### 配置步骤

点击左侧菜单 **⚙️ AI 设置**：

1. **选择服务商**：DeepSeek 或 阿里百炼/千问
2. **填写 API Key**：Key 仅保存在本机，不上传任何服务器
3. **（可选）填写中转站地址**：见下方说明
4. 点击「**测试连接**」确认可用
5. 点击「**保存并应用**」立即生效

也可通过环境变量或 `.env` 文件配置，见[环境变量参考](#环境变量参考)。

---

## 中转站 / 网关配置

如果无法直连官方 API，可使用任意兼容 OpenAI 格式的中转代理。

### 填写方式

在「⚙️ AI 设置 → 第三步（可选）」中填写：

```
https://api.your-proxy.com/v1
```

**注意事项：**

- 地址需以 `http://` 或 `https://` 开头
- 若路径不含 `/v1`，系统自动补全
- 一个地址对 DeepSeek 和千问同时生效，无需分别配置
- 留空则直连官方接口

### 常见错误排查

| 错误提示 | 原因 | 解决方案 |
|----------|------|----------|
| 路径错误（404） | Base URL 缺少 `/v1` 路径 | 检查地址是否以 `/v1` 结尾 |
| 鉴权失败（403） | API Key 与中转站不匹配 | 确认 Key 与该中转站对应 |
| 网关故障（5xx） | 中转站服务暂时不可用 | 稍后重试，或清空地址改为直连 |
| 连接超时 | 中转站地址不可达 | 检查地址拼写，确认网络通畅 |

---

## 数据与隐私

| 数据类型 | 存储位置 | 说明 |
|---------|---------|------|
| API Key / 中转站地址 | `data/settings.json` | 仅本机，从不上传 |
| 历史任务 / 草稿 | `data/copilot.db` | 本地 SQLite |
| 图片缓存 | `data/images_cache/` | 优化后的截图，本地缓存 |
| 导出报告 | `我的报告/YYYY-MM-DD/` | 按日期归档，Word 格式 |

**所有数据均在本机运行，不经过任何第三方服务器。**
唯一的网络请求是您主动配置的 AI API 调用（DeepSeek / 阿里云）。

---

## 项目结构

```
light-quant-copilot/
│
├── run.bat                        # Windows 一键启动
├── run.sh                         # Linux / macOS / WSL 一键启动
├── requirements.txt               # Python 依赖
├── .env.example                   # 环境变量模板（复制为 .env 使用）
│
├── app/
│   ├── ui.py                      # Streamlit 主入口 + 路由 + 全局错误保护
│   └── logic/
│       ├── morning_report.py      # 晨报向导（4步 Wizard）
│       ├── risk_control.py        # 风控向导（4步 Wizard）
│       ├── review.py              # 复盘向导（4步 Wizard）
│       ├── chat_ui.py             # Step3 共享聊天 UI（草稿 + 多轮对话）
│       ├── export.py              # Word 导出 + 专业排版 + 归档
│       ├── image_utils.py         # 图片预处理（缩放 + 压缩，无需 OCR）
│       ├── providers.py           # AI 提供商层（Mock / Real / ProviderProxy）
│       ├── settings.py            # 配置读写（settings.json）
│       ├── settings_ui.py         # AI 设置页面 UI
│       ├── state_manager.py       # 草稿自动暂存 + 会话恢复
│       ├── db.py                  # SQLite 数据访问层（DAO）
│       └── llm/                   # LLM 接入子包
│           ├── config.py          # LLMConfig 数据类 + normalize_base_url
│           ├── router.py          # 多 Provider 路由 + 网关 URL 解析
│           ├── prompts.py         # System Prompt + 数据格式化 + 响应解析
│           ├── deepseek.py        # DeepSeek API 客户端
│           ├── qwen.py            # 阿里千问 API 客户端
│           └── pipeline.py        # LLM 生成流水线（校验 + 重试 + 审计）
│
├── data/                          # 运行时自动创建
│   ├── copilot.db                 # SQLite 数据库（历史任务 + 草稿）
│   ├── settings.json              # 持久化 AI 配置（含 API Key）
│   └── images_cache/              # 截图预处理缓存
│
└── 我的报告/                       # 导出报告归档（运行时自动创建）
    └── YYYY-MM-DD/
        ├── YYYY-MM-DD_晨报.docx
        ├── YYYY-MM-DD_风控清单.docx
        └── YYYY-MM-DD_收盘复盘.docx
```

---

## 环境变量参考

复制 `.env.example` 为 `.env` 并填写，或直接在「⚙️ AI 设置」界面配置（推荐）。

```bash
# Provider 选择：mock（默认）| deepseek | qwen
LLM_PROVIDER=mock

# DeepSeek
DEEPSEEK_API_KEY=sk-xxxxxxxx
DEEPSEEK_MODEL=deepseek-chat          # 默认值
DEEPSEEK_BASE_URL=                    # 留空直连官方；填写则覆盖中转站设置

# 阿里千问 / 百炼
QWEN_API_KEY=sk-xxxxxxxx
QWEN_MODEL=qwen-plus                  # 默认值
QWEN_BASE_URL=                        # 同上

# 中转站（统一覆盖所有 Provider）
GATEWAY_BASE_URL=https://api.your-proxy.com/v1

# 生成参数（可选）
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=2048
LLM_TIMEOUT=60
```

> **优先级**：`data/settings.json`（UI 保存）> `.env` 文件 > 系统环境变量

---

## 版本历史

| 版本 | 主要变更 |
|------|---------|
| **v0.2.0** | 真实 LLM 接入（DeepSeek / 千问）；多轮对话草稿；中转站/网关 URL 支持；Word 专业排版重构（`ExportResult` + 安全文件名 + 归档）；图片截图上传识别；会话自动暂存与恢复；全局模块错误隔离 |
| **v0.1.1** | 新增 `run.bat` / `run.sh` 一键启动脚本（Python 版本检测、自动 venv、WSL 适配） |
| **v0.1.0** | 骨架版，MockProvider 占位输出，三模块向导可跑通 |
