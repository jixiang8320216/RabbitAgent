import os
import sys
from pathlib import Path

# 用户数据目录（用途：sessions、logs、用户级skills/plugins）
def get_user_data_home()->Path:
    """获取用户数据目录(Sessions、logs、用户级skills/plugins)

    统一使用~/.rabbit/作为用户数据目录：
    - 路径简洁、跨平台一致
    - 用户易于查找和操作

    支持环境变量覆盖：
    1.&RABBIT_USER_HOME(用户指定，最高优先级)
    2.~/.rabbit(默认)
    Returns:
        用户数据目录路径
    """
    # 1. 环境变量最高优先级
    if os.getenv("RABBIT_USER_HOME"):
        path = Path(os.getenv("RABBIT_USER_HOME"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 2. 默认:~/.rabbit
    path = Path.home()/".rabbit"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_app_storage_dir()->Path:
    return get_apps_dir()

def get_apps_dir()->Path:
    if os.environ.get("RABBIT_APPS_DIR"):
        apps_dir = Path(os.environ.get("RABBIT_APPS_DIR"))
    elif os.environ.get("RABBIT_SESSIONS_DIR"):
        apps_dir = Path(os.environ.get("RABBIT_SESSIONS_DIR"))
    else:
        apps_dir = get_user_data_home()/"apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    return apps_dir


def get_logs_dir():
    logs_dir =get_user_data_home()/"logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_log_file(port:int|None=None)->Path:
    """
    获取日志文件的路径：{user_data}/logs/rabbit-{port}.log
    支持多实例Agent运行场景的日志拆分
    Args：
        port: 端口号，区分实例。
    :return:
           日志文件路径
    """
    if port is not None:
        return get_logs_dir()/f"rabbit-{port}.log"
    return get_logs_dir()/"rabbit.log"



def get_config_dir()->Path:
    """获取rabbit agent的包内置配置目录。

    ``shared/paths.py`` 位于 ``<项目根>/shared/paths.py``，因此
    ``parents[0]`` = ``shared``、``parents[1]`` = 项目根。

    此前写的是 ``parents[2]``，多退了一级 —— 在
    ``D:/AI/AIProject/RabbitAgent`` 这样的布局下解析成
    ``D:/AI/AIProject/conf``（项目**外面**的目录），于是
    ``conf/logging.ini`` 永远找不到，``logging.config.fileConfig`` 从未执行，
    root logger 没有任何文件 handler：所有 ``logger.error`` /
    ``logger.exception`` 的堆栈全部丢失，TUI 出错时只剩 spinner，
    事后也无处可查。
    """
    return Path(__file__).resolve().parents[1]/"conf"

def get_bundled_mcp_node_bin_dir()->Path|None:
    """发现the package MCP Node executable directory， when present."""

    executable_dir = Path(sys.executable).resolve().parent
    for ancestor in (executable_dir,*executable_dir.parents):
        candidate = ancestor/"tools"/"mcp_node_packages"/"node_modules"/".bin"
        if candidate.is_dir():
            return candidate
    return None
"""获取用户配置目录(与数据目录相同)"""
get_user_config_dir = get_user_data_home
get_user_data_dir = get_user_data_home
get_package_config_dir = get_config_dir