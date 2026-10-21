#!/usr/bin/env bash
#rabbit-setup - Install rabbit-agent CLI and create symlink and its dependencies in a virtual environment.

#This script 
#1. Create virtual environment in .venv directory
#2. Install rabbit-agent and its dependencies in the virtual environment
#3. Create a symlink to /usr/local/bin/rabbit-cli


set -Eeuo pipefail
# Use system Python
export UV_PYTHON_DOWNLOAD=never
export UV_PYTHON=$(which python3)


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
VENV="$PROJECT_DIR/.venv"
PYTHON_BIN=""

echo "RabbitAgent 安装程序"
echo "项目目录：$SCRIPT_DIR"

# Check if Python is  available
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "错误：未找到 Python。"
    exit 1
fi

# Check Python version
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "错误：Python 版本必须 >= 3.12。"
    "$PYTHON_BIN" --version
    exit 1
fi

echo "Python：$("$PYTHON_BIN" --version)"

# Check if uv is available
if command -v uv >/dev/null 2>&1; then
    USE_UV=1
    echo "检测到 uv，将使用快速安装路径。"
else
    USE_UV=0
    echo "未检测到 uv，将回退到 pip。"
fi

# step1: Create virtual environment
echo "创建虚拟环境..."
if [[ -d "$VENV" ]]; then
    read -r -p ".venv 已存在，是否重建？[y/N] " answer

    if [[ "$answer" =~ ^[Yy]$ ]]; then
        if pgrep -f "$VENV" >/dev/null 2>&1;  then
            echo "检测到虚拟环境正在使用或被锁定，跳过重建。"
        else
            echo "虚拟环境未被锁定，删除并重建。"
            rm -rf "$VENV"

        fi
    else
        echo "保留现有虚拟环境。"
    fi
fi

if [[ ! -d "$VENV" ]]; then
    if [[ "$USE_UV" == "1" ]]; then
        uv venv "$VENV" --python "$PYTHON_BIN"
    else
        "$PYTHON_BIN" -m venv "$VENV"
    fi
fi

# step2: Activate virtual environment and install dependencies
source "$VENV/bin/activate"

if [[ "$USE_UV" == "1" ]]; then
    echo " 使用 uv 安装依赖..."
    cd "$PROJECT_DIR"
    if [[ -f "$PROJECT_DIR/pyproject.toml" ]]; then
        uv pip install -e "."
    else
        echo "未检测到 pyproject.toml，跳过可编辑安装（项目以 python main.py 方式运行）。"
    fi
    if [[ -f "$PROJECT_DIR/requirements.txt" ]]; then
        uv pip install -r "$PROJECT_DIR/requirements.txt"
    fi
    echo "[INFO] 依赖安装完成。"
else
    # 配置 pip 使用镜像源（与 rabbit-web 一致）
    export PIP_CONFIG_FILE=/dev/null
    export PIP_NO_INPUT=1
    export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"

    #step2: upgrade pip
    pip install $PIP_EXTRA_ARGS --upgrade pip

    #step3: install Rabbit package and its dependencies
    if [[ -f "$PROJECT_DIR/pyproject.toml" ]]; then
        pip install $PIP_EXTRA_ARGS -e "$PROJECT_DIR"
    else
        echo "未检测到 pyproject.toml，跳过可编辑安装（项目以 python main.py 方式运行）。"
    fi
    if [[ -f "$PROJECT_DIR/requirements.txt" ]]; then
        pip install $PIP_EXTRA_ARGS -r "$PROJECT_DIR/requirements.txt"
    fi
fi

# step4 Create symlink to /usr/local/bin
SYMLINK_PATH="/usr/local/bin/rabbit-cli"
RABBIT_CLI_PATH="$PROJECT_DIR/rabbit-cli.sh"

chmod +x  "$RABBIT_CLI_PATH"

echo "创建符号链接：$SYMLINK_PATH -> $RABBIT_CLI_PATH"

if [ -L "$SYMLINK_PATH" ]; then
    echo "符号链接已存在，删除旧的符号链接。"
    EXISTING_TARGET=$(readlink "$SYMLINK_PATH")
    if [ "$EXISTING_TARGET" != "$RABBIT_CLI_PATH" ]; then
        echo "警告：现有符号链接指向 $EXISTING_TARGET，而不是 $RABBIT_CLI_PATH。"
        read -r -p "是否继续并覆盖现有符号链接？[y/N] " answer
        echo
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            echo "覆盖现有符号链接..."
            sudo rm "$SYMLINK_PATH"
            sudo ln -s "$RABBIT_CLI_PATH" "$SYMLINK_PATH"
            echo "符号链接已更新。"
        else
            echo "操作已取消。请手动处理符号链接。"
            exit 1
        fi
    fi
    sudo rm "$SYMLINK_PATH"
elif [ -f "$SYMLINK_PATH" ]; then
    echo "错误：$SYMLINK_PATH 已存在且不是符号链接。请手动删除或重命名该文件。"
    exit 1
else
   # Create the symlink
   if sudo ln -s "$RABBIT_CLI_PATH" "$SYMLINK_PATH" ; then
       echo "符号链接创建成功。"
   else
       echo "错误：无法创建符号链接。请检查权限。"
       exit 1
   fi
fi



#Verify instation
echo "Verifying installation..."
if command -v rabbit-cli >/dev/null ; then
    echo "rabbit-cli 已安装。"
else
    echo "错误：rabbit-cli 未安装。请检查安装日志。"
    exit 1
fi

echo ""
echo "RabbitAgent 安装完成。"
echo ""
echo "Usage:"
echo " 1. Navigate to the project directory.: cd /path/to/your/project"
echo " 2. Run the CLI: rabbit-cli"
echo ""
echo "the CLI will use your current directory as the working directory."