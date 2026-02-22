#!/usr/bin/env bash
# ============================================================
#  量化副驾 · Linux / WSL 一键启动器
# ============================================================
# 用法：bash run.sh
# ============================================================

# 脚本所在目录（兼容软链接与空格路径）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 颜色输出 ─────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC}  $*"; }
err()  { echo -e "${RED}[错误]${NC} $*"; }

echo
echo "=============================="
echo "  量化副驾 · 一键启动器"
echo "=============================="
echo

# ── 1. 检测 Python ─────────────────────────────────────────
PYTHON_CMD=""
for cmd in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_CMD="$cmd"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    err "未找到 Python"
    echo
    echo "请安装 Python 3.11 或更高版本："
    echo "  Ubuntu/Debian :  sudo apt update && sudo apt install python3.11"
    echo "  官网下载       :  https://www.python.org/downloads/"
    exit 1
fi

# ── 2. 检查版本 ────────────────────────────────────────────
PYMAJOR=$("$PYTHON_CMD" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
PYMINOR=$("$PYTHON_CMD" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
PYVER="${PYMAJOR}.${PYMINOR}"

if [ -z "$PYMAJOR" ]; then
    err "无法读取 Python 版本，请确认 Python 安装完整"
    exit 1
fi

if [ "$PYMAJOR" -lt 3 ] || { [ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 11 ]; }; then
    err "Python 版本过低（当前 ${PYVER}，需要 3.11+）"
    echo
    echo "请升级 Python："
    echo "  Ubuntu/Debian :  sudo apt install python3.11"
    echo "  官网下载       :  https://www.python.org/downloads/"
    exit 1
fi

ok "Python ${PYVER} 检测通过"

# ── 3. 创建虚拟环境 ────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
VPYTHON="$VENV_DIR/bin/python"
VPIP="$VENV_DIR/bin/pip"
VSTREAMLIT="$VENV_DIR/bin/streamlit"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "正在创建虚拟环境..."
    if ! "$PYTHON_CMD" -m venv "$VENV_DIR" 2>/dev/null; then
        err "虚拟环境创建失败"
        echo
        echo "常见原因：缺少 venv 模块。请运行："
        echo "  Ubuntu/Debian:  sudo apt install python3.11-venv"
        exit 1
    fi
    ok "虚拟环境已创建"
fi

# ── 4. 安装依赖 ────────────────────────────────────────────
echo "正在检查并安装依赖（首次启动可能需要几分钟）..."
if ! "$VPIP" install -r "$SCRIPT_DIR/requirements.txt" -q; then
    err "依赖安装失败"
    echo
    echo "请检查网络连接，或手动运行："
    echo "  source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
ok "依赖已就绪"

# ── 5. 初始化数据库（幂等）────────────────────────────────
if ! "$VPYTHON" -c \
    "import sys; sys.path.insert(0, '${SCRIPT_DIR}'); from app.logic.db import init_db; init_db()" \
    2>/dev/null; then
    warn "数据库初始化遇到问题，但不影响首次使用"
else
    ok "数据库已就绪"
fi

# ── 6. WSL 检测 ────────────────────────────────────────────
IS_WSL=0
if grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; then
    IS_WSL=1
fi

# ── 7. 启动 Streamlit ──────────────────────────────────────
echo
echo "=============================="
echo "  正在启动量化副驾..."
echo "=============================="
echo
echo "  访问地址：http://localhost:8501"
if [ "$IS_WSL" -eq 1 ]; then
    echo
    warn "检测到 WSL 环境"
    echo "     请在 Windows 浏览器中手动打开：http://localhost:8501"
fi
echo
echo "  按 Ctrl+C 停止服务"
echo "=============================="
echo

export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
"$VSTREAMLIT" run "$SCRIPT_DIR/app/ui.py"
