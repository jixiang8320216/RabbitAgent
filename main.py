"""RabbitAgent command line entry point."""

import asyncio
import logging
import os
from typing import Optional

from domains.configuration.infrastructure.logging_bootstrap import LOGGER_CONFIG
from interfaces.cli.args_parse import build_argument_parser
from domains.configuration.infrastructure.store.user_config_store import load_or_create_user_config
from domains.session.infrastructure.session_persistence import SessionPersistence
import domains.llm.provider.domain as provider_domain
from domains.context import build_runtime, build_runtime_context
from domains.observability import initialize_telemetry

from domains.configuration import get_default_mode,get_app_storage_dir
import domains.configuration.infrastructure.adapter.app_mode_resolver as app_mode_resolver

logger = logging.getLogger(__name__)


def _cli_log_filter():
    """
    日志过滤
    """
    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler) \
           and not isinstance(handler, logging.FileHandler):
            handler.setLevel(logging.ERROR)


def _remove_comsole_handlers(logger_name: Optional[str]=None) -> None:
    """Remove all StreamHandler from a logger to prevent console output"""
    target_logger = logging.getLogger(logger_name or __name__)
    handles_to_remove = [
        h
        for h in target_logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    for h in handles_to_remove:
        target_logger.removeHandler(h)

def _get_active_profile(user_config:dict) -> dict:
    llm_configs= user_config.get("llmConfigs",[])
    if not llm_configs or not isinstance(llm_configs,list):
        return None
    active_profile = provider_domain.select_prefered_active_mapping(llm_configs)
    if active_profile is  not None:
        return active_profile
    return llm_configs[0]

# 解析--session <id|prefix> 参数， 从本地session存储中查找匹配会话，返回三元组（session_id,project_home, is_resume）,供CLI启动路径决定是新建会话还是恢复已有会话并切换工作目录。
def  _resolve_session_arg(args_session: str|None) ->tuple[str|None,str, bool]:
     """会话选择

     注意：``SessionPersistence`` 必须按**类**导入。
     原先这里写成 ``import ...session_persistence as SessionPersistence``，
     把模块别名成了类名，随后 ``SessionPersistence.list_sessions(...)``
     会抛 ``AttributeError: module has no attribute 'list_sessions'``。
     """
     if not args_session:
         return None, os.getcwd(), False
     sessions_dir = get_app_storage_dir()
     session_rows = SessionPersistence.list_sessions(
         sessions_dir=sessions_dir,
         limit= 100000,
         include_old_format=True,
     )
     mathed_session_id=None
     for session_row in session_rows:
         full_session_id = session_row.get("id")
         if not isinstance(full_session_id, str):
             continue
         if full_session_id and (full_session_id.startswith(args_session) or full_session_id==args_session):
            mathed_session_id = full_session_id
            break
     if not mathed_session_id:
         print(f"Session ID not found: {args_session}")
         print("Use'realy-cli'and '/session' to list avalible sessions")
         return None, os.getcwd(), False
     session_working_dir = SessionPersistence.get_project_home(mathed_session_id)
     if session_working_dir:
         project_home= session_working_dir
         print(f" Resuming session ID: {mathed_session_id[:8]} (project:{project_home})")
     else:
         project_home = os.getcwd()
         print(f" Resuming session ID: {mathed_session_id[:8]}")

     return mathed_session_id, project_home,True

def _remove_console_handlers(logger_name: Optional[str]=None):
    target_logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
    handlers_to_remove = [
        h
        for h in target_logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    for handler in handlers_to_remove:
        target_logger.removeHandler(handler)

async def run_app(app_config, project_home: str) -> None:
    """
    运行tui
    """
    from interfaces.cli.tui_application import TUIApplication
    cli_app= TUIApplication(app_config,project_home=project_home)
    _remove_console_handlers()
    await cli_app.run()


async def asyncio_main():
    # 1. 配置控制台日志过滤
    _cli_log_filter()

    # 2. 命令行参数解析
    args = build_argument_parser().parse_args()
    try:
        #3 加载用户config配置信息
        user_config = load_or_create_user_config()
        #4. 解析应用启动模式(配置+环境变量)
        project_home = os.getcwd()
        app_mode = app_mode_resolver.load_app_mode(user_config)
        #5 安装临时RuntimeContext(供会话查询用)
        build_runtime_context()
        #6 解析 --session 参数（前缀匹配）
        session_id,project_home,is_resume = _resolve_session_arg(args.session)
        if args.session and session_id is None:
            return 1
        # 7. 构建完整运行时： harness 启动器的运行时装配总入口。把项目路径、session、模式、模型、MCP、插件、内存等所有上下文统一组装成AppConfig+ACPRuntimeDescriptor
        #合体的ACPRuntimeBuild，供ACP runtime启动消费。是CLI/ACP host启动路径上的核心编排函数。
        runtime = await build_runtime(
            project_home=project_home,
            session_id=session_id,
            is_resume= is_resume,
            current_mode_id=app_mode or get_default_mode(),
        )
        app_config = runtime.app_config
        #8. 初始化遥测（依赖RuntimeContext）
        try:
            initialize_telemetry(entry_point="cli")
        except Exception as telemetry_error:
            logger.warning(f"Failed to initialize telemetry : {telemetry_error}")

        # 9 应用运行
        await run_app(app_config, project_home)

    except KeyboardInterrupt:
        print("\n\n Interrupted by user. Goodbye!")
    except Exception as e:
        logging.error(f"Fatal error: {e}",exc_info=True)
        print(f"\n Fatal error: {e}")
        return 1
    return 0

def main() ->int:
    return asyncio.run(asyncio_main())


if __name__ == '__main__':
    exit(main())



