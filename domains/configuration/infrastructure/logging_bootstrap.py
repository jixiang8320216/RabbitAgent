"""日志与进程环境引导。

在应用 bootstrap 之前完成三件事：

1. 加载 ``.env`` 到进程环境
2. 压制 asyncio 调试噪声
3. 装配 ``logging.ini``（若包内置配置存在）

CLI 的 ``config`` 子命令解析与执行属于**接口层**职责，不在本模块。
"""

import logging
import logging.config
import os

from dotenv import load_dotenv

from shared.paths import get_config_dir, get_log_file


def _config_logger_before_bootstrap() -> bool:
    """启动之前加载项。"""
    load_dotenv()
    os.environ["PYTHONASYNCIODEBUG"] = "0"
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    log_config_path = get_config_dir() / "logging.ini"
    if os.path.exists(log_config_path):
        logging.config.fileConfig(
            log_config_path.as_posix(),
            disable_existing_loggers=False,
            defaults={"agent_log_file": get_log_file().as_posix()},
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers:
        handler.setLevel(logging.INFO)
    return True


LOGGER_CONFIG = _config_logger_before_bootstrap()
