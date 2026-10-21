#!/usr/bin/env bash
# rabbit-cli  Startup script for RabbitAgent CLI
#
# This script is designed to be symlinked to /usr/local/bin/rabbit-cli for easy access.
# It locates the virtual environment next to this script (or upward) and runs main.py.

# 退出码透传
set +e

DEBUG_MODE=0
if [[ -n "${DEBUG_MODE:-}" ]]; then
   DEBUG_MODE=1
fi

SCRIPT_PATH="${BASH_SOURCE[0]}"

if command -v readlink >/dev/null 2>&1; then
    SCRIPT_PATH="$(readlink -f "$SCRIPT_PATH")"
else
    SCRIPT_PATH="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)/$(basename "$SCRIPT_PATH")"
fi

INSTALL_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

# 从脚本所在目录向上查找包含 .venv 的项目目录
SEARCH_DIR="$INSTALL_DIR"
while [[ "$SEARCH_DIR" != "/" ]]; do
    if [[ -x "$SEARCH_DIR/.venv/bin/python" ]]; then
        PROJECT_DIR="$SEARCH_DIR"
        VENV="$SEARCH_DIR/.venv"
        break
    fi
    SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done

# 若未找到，退回到脚本所在目录
if [[ -z "${PROJECT_DIR:-}" ]]; then
    PROJECT_DIR="$INSTALL_DIR"
    VENV="$INSTALL_DIR/.venv"
fi

if [[ "$DEBUG_MODE" == "1" ]]; then
    echo "[DEBUG] SCRIPT_PATH=$SCRIPT_PATH"
    echo "[DEBUG] INSTALL_DIR=$INSTALL_DIR"
    echo "[DEBUG] PROJECT_DIR=$PROJECT_DIR"
    echo "[DEBUG] VENV=$VENV"
fi

# 判断虚拟环境激活脚本路径
VENV_ACTIVATE=""
if [[ -f "$VENV/bin/activate" ]]; then
    VENV_ACTIVATE="$VENV/bin/activate"
elif [[ -f "$VENV/Scripts/activate" ]]; then
    VENV_ACTIVATE="$VENV/Scripts/activate"
else
    echo "错误：未找到虚拟环境激活脚本。"
    echo "已检查：$VENV/bin/activate 和 $VENV/Scripts/activate"
    exit 1
fi

if [[ ! -f "$VENV_ACTIVATE" ]]; then
    echo "错误：虚拟环境不完整：$VENV"
    exit 1
fi

# 激活虚拟环境
source "$VENV_ACTIVATE"
if [ $? -ne 0 ]; then
    echo "错误：无法激活虚拟环境：$VENV"
    exit 1
fi

# 设置项目环境变量并重建 PYTHONPATH（仅把项目目录置于最前）
export RABBIT_PROJECT_DIR="$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"

if [[ "$DEBUG_MODE" == "1" ]]; then
    echo "[DEBUG] RABBIT_PROJECT_DIR=$RABBIT_PROJECT_DIR"
    echo "[DEBUG] PYTHONPATH=$PYTHONPATH"
fi

# 校验入口文件 main.py 是否可导入
if ! ( cd "$PROJECT_DIR" && python -c "import main" ) 2>/dev/null; then
    echo "错误：无法导入 main（入口文件 main.py 不存在或依赖缺失）。"
    echo "项目目录：$PROJECT_DIR"
    if [[ "$DEBUG_MODE" == "1" ]]; then
        echo "Attempting to list detail error for import main:"
        ( cd "$PROJECT_DIR" && python -c "import main" )
    fi
    exit 1
fi

# 强制 UTF-8
export PYTHONIOENCODING="utf-8"

python "$PROJECT_DIR/main.py" "$@"
EXIT_CODE=$?
exit "$EXIT_CODE"
