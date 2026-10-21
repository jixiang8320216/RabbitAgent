"""进程属主探测。

判定「当前遥测数据由谁产生」—— 容器化部署里是 pod 名，
裸机部署里是主机名，CI 里是流水线标识。

这个维度用于区分同一套部署里的多个实例：数据量大时能立刻判断
「是我这个实例的问题还是整体的」。

**历史缺口**：本模块此前是空文件，``composition`` 调用
``ProcessOwnerService().get_process_owner()`` 时抛 AttributeError。
"""

from __future__ import annotations

import os
import socket

# 环境变量优先级从高到低：容器编排注入的显式标识最可靠
_OWNER_ENV_KEYS = (
    "POD_NAME",
    "HOSTNAME",
    "RELEASE_NAME",
    "CI_JOB_ID",
    "GITHUB_RUN_ID",
)


class ProcessOwnerService:
    """进程属主探测服务。"""

    def get_process_owner(self) -> str | None:
        """取进程属主标识；探测不到时返回 ``None``。"""
        # 1) 编排平台注入的环境变量
        for key in _OWNER_ENV_KEYS:
            value = os.environ.get(key)
            if value and value.strip():
                return value.strip()

        # 2) 容器内可读 /etc/hostname，通常就是 pod 名
        try:
            with open("/etc/hostname", "r", encoding="utf-8") as handle:
                hostname = handle.read().strip()
            if hostname:
                return hostname
        except (OSError, UnicodeDecodeError):
            pass

        # 3) 退回到 socket 主机名
        try:
            hostname = socket.gethostname()
        except OSError:
            return None
        return hostname.strip() or None


__all__ = ["ProcessOwnerService"]
