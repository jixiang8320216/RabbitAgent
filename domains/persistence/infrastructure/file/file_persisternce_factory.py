from __future__ import annotations

import logging

from domains.persistence.application.persistence_plane import PersistencePlane

logger = logging.getLogger(__name__)


def create_file_persisternce_plane() -> PersistencePlane:
    """构造文件实现（file-backed）的持久化平面。

    参数名/函数名 `persisternce` 为历史拼写，与
    `rabbit_application_composition` 的调用保持一致。

    当前 hook 相关的文件 store（hook_state / hook_run / session_event）
    尚未实现，因此按「能力未就绪即降级」的启动策略返回带空 store 的平面：
    `rabbit_application_composition` 检测到 store 为 None 后只会记录
    "confiruration Hook runtime is unavailable" 警告并跳过 hook 装配，
    不影响后续初始化和会话流程。
    """
    return PersistencePlane(hook_state=None, hook_run=None, session_event=None)
