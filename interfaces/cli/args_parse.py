import os
import argparse
from argparse import Namespace
import pathlib
import json
import typing
import pydantic
import domains.hook as hook
import domains.hook.application as hook_application
from pathlib import Path

from domains.session.infrastructure.session_persistence import SessionPersistence
from domains.runtime.application.rabbit_application import RabbitApplication
from domains.workspace.application import WorkspaceVersionService,build_workspace_version_service
from domains.workspace.infrastructure import FileCheckpointStore
from domains.workspace.domain.checkpoint_models import CheckpointReason

from domains.configuration import get_app_storage_dir,get_default_mode
from domains.context import bootstrap_context, build_runtime

def _enum_values(enum_type:type[typing.Any]) -> list[str]:
    return [str(item.value) for item in enum_type]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Rabbit-cli",
        description="RabbitAgent 命令行客户端",
    )
    parser.add_argument(
        "-c",
        "--command",
        type=str,
        help="非交互模式：执行一条命令后退出",
    )
    parser.add_argument(
        "-s",
        "--session",
        type=str,
        help="恢复指定的历史会话",
    )
    parser.add_argument(
        "--f",
        "--format",
        type=str,
        dest="output_format",
        choices=("json", "text"),
        default="text",
        help="非交互模式的输出格式（默认：text）",

    )
    return parser


def config_config_parser(subparsers) -> None:
    """
    注册config子命令  ，举例： rabbit-cli config provider openapi
    :param subparsers:
    :return:
    """
    config_parser = subparsers.add_parser(
        "config", help="config provider"
    )
    config_subparsers = config_parser.add_subparsers(dest="config_action")

    provider_parser = config_subparsers.add_parser(
        "provider", help="设置默认的LLM提供商")
    provider_parser.add_argument(
        "provider", type=str, choices=["openai", "anthropic"], help="LLM提供商名称")


async def execute_config_command(args) -> int:
    from interfaces.commands import ConfigCommand


    config_args: list[str] = [
    ]
    if getattr(args, "config_action", None) == "provider":
        config_args = ["provider", args.provider]

    bootstrap_context()
    result = await ConfigCommand.execute(config_args)
    if result.message:
        print(result.message)
    return 0 if result.success else 1

def config_hook_parser(subparsers) -> None:
    hooks_parser = subparsers.add_parser("hooks", help="Manage App-scoped configured hooks")
    hooks_parser.add_argument("-s","--session",dest="hook_session", help="Extra Session ID")

    _config_hook_actions(hooks_parser)


async def _action_test(
    args: argparse.Namespace,
    managerment: hook_application.HookManagementUseCases,
    session_id: str,
)->pydantic.BaseModel:
    #todo
    ...

async def _action_runs(
            args: argparse.Namespace,
            managerment: hook_application.HookManagementUseCases,
            session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...

async def _action_runs(
        args: argparse.Namespace,
        managerment: hook_application.HookManagementUseCases,
        session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...


async def _action_toggle(
        args: argparse.Namespace,
        managerment: hook_application.HookManagementUseCases,
        session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...

async def _action_import(
        args: argparse.Namespace,
        managerment: hook_application.HookManagementUseCases,
        session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...

async def _action_validate(
        args: argparse.Namespace,
        managerment: hook_application.HookManagementUseCases,
        session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...

async def _action_show(
        args: argparse.Namespace,
        managerment: hook_application.HookManagementUseCases,
        session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...

async def _action_list(
        args: argparse.Namespace,
        managerment: hook_application.HookManagementUseCases,
        session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...

async def _action_capabilities(
        args: argparse.Namespace,
        managerment: hook_application.HookManagementUseCases,
        session_id: str,
) -> pydantic.BaseModel:
    # todo
    ...



async def _action_claude_live(
    args: argparse.Namespace,
    managerment: hook_application.HookManagementUseCases,
    session_id: str,
)->pydantic.BaseModel:
    del session_id
    scope = hook.HookScope(args.scope)
    current = await managerment.read_configuration(scope)
    return await managerment.set_claude_live(
       hook_application.HookClaudeLiveRequest(
           scope=scope,
           enabled= args.state =="enable",
           expected_etag = args.etag or current.etag
       )
    )

async def _action_debug(
    args:argparse.Namespace,
    managerment: hook_application.HookManagementUseCases,
    session_id: str,
)->pydantic.BaseModel:
    ttl_seconds = None if args.ttl.lower() =="off" else int(args.ttl)
    return await managerment.set_debug_ttl(
        session_id,
        hook_application.HookDeBugTtlRequest(ttl_seconds=ttl_seconds),
    )

_HOOK_ACTION_HANDLERS:dict[str,typing.Callable]={
    "capabilities":_action_capabilities,
    "list":_action_list,
    "show": _action_show,
    "validate": _action_validate,
    "import": _action_import,
    "enable":_action_toggle,
    "disable": _action_toggle,
    "runs": _action_runs,
     "test": _action_test,
     "claude-live": _action_claude_live,
     "debug":_action_debug,
}
def render_hook_result(result:pydantic.BaseModel, *, output_format:str="text") ->str:
    """"""
    #todo

def _config_hook_actions(parse: argparse.ArgumentParser) -> None:
    actions_parser = parse.add_subparsers(dest="hook_action", required=True)

    def add_action(name:str, *, help_text:str)->argparse.ArgumentParser:
        action_parser = actions_parser.add_parser(name, help=help_text)
        action_parser.add_argument(
            "-s",
            "--session",
            dest="action_session",
            help=argparse.SUPPRESS,
        )

        action_parser.add_argument(
            "--format",
            dest="action_format",
            choices=["json", "text"],
            help=argparse.SUPPRESS,
        )
        return action_parser

    add_action("capabilities", help_text="Show all 30 event capabilities")
    list_parser = add_action("list", help_text="List compiled Hooks")
    list_parser.add_argument("--event", choices=_enum_values(hook.HookEvent))
    list_parser.add_argument("--scope", choices=_enum_values(hook.HookScope))
    list_parser.add_argument("--source", choices=_enum_values(hook.HookSourceKind))
    list_parser.add_argument("--enabled", choices=("true", "false"))

    show_parser = add_action("show", help_text="Show one stable Hook ID")
    show_parser.add_argument("hook_id")

    validate_parser = add_action("validate", help_text="Validate a full JSON scope document")
    validate_parser.add_argument("path",  help="JSON file path,or '-' for stdin")
    validate_parser.add_argument("--scope", required=True, choices=_enum_values(hook.HookScope))
    import_parse = add_action(
        "import",
        help_text="Preview or apply a Claude/legacy import",
    )
    import_parse.add_argument("--source-format", required=True, choices=_enum_values(hook_application.HookImportFormat))
    import_parse.add_argument("--target_scope", required=True, choices=_enum_values(hook.HookScope))
    import_parse.add_argument("--source_scope", choices=_enum_values(hook.HookScope))
    import_parse.add_argument("--source_file")
    import_parse.add_argument("--source_uri")
    import_parse.add_argument("--etag")
    import_parse.add_argument("--apply",action="store_true")


    for action in ("enable","disable"):
        toggle_parser = add_action(action,help_text=f"{action.title()} one stable Hook ID")
        toggle_parser.add_argument("hook_id")
        toggle_parser.add_argument("scope",default=hook.HookScope.PROJECT.value,choices=_enum_values(hook.HookScope))
        toggle_parser.add_argument("--etag")

    runs_parser = add_action("runs", help_text="List durable Session Hook runs")
    runs_parser.add_argument("--hook-id")
    runs_parser.add_argument("--event", choices=_enum_values(hook.HookEvent))
    runs_parser.add_argument("--status", action="append", choices=_enum_values(hook.HookRunStatus))
    runs_parser.add_argument("--test", choices=("true", "false"))
    runs_parser.add_argument("--offset", type=int,default=0)
    runs_parser.add_argument("--limit", type=int,default=100)


    test_parser = add_action("test", help_text="Execute one confirmed synthetic Hook test")
    test_parser.add_argument("hook_id")
    test_parser.add_argument("--input", required=True, help="Typed Hook input JSON file")
    test_parser.add_argument("--correlation-id")
    test_parser.add_argument("--confirm",action="store_true")


    claude_parser = add_action("claude_live", help_text="Toggle read-only Claude live hook discovery")
    claude_parser.add_argument("state", choices=("enable", "disable"))
    claude_parser.add_argument("--scope", default=hook.HookScope.PROJECT.value)
    claude_parser.add_argument("--etag")


    debug_parser = add_action("debug", help_text="Set Session full-payload retention TTL")
    debug_parser.add_argument("ttl", help="TTL seconds, or 'off'")

def _resolve_session_id(value:str)->str:
    rows =SessionPersistence.list_sessions(
        sessions_dir=get_app_storage_dir(),
        limit=100_000,
        include_old_format = True,
    )
    exact = [str(row["id"] for row in rows if row.get("id") ==value)]
    if exact:
        return exact[0]
    mathes=[str(row["id"]) for row in rows if str(row.get("id","")).startswith(value)]
    if not mathes:
        raise LookupError(f'Could not find session id: {value}')
    if len(mathes) > 1:
        raise LookupError(f'Found more than one session id: {value}')
    return mathes[0]

async def execute_hook_action(
    args: argparse.Namespace,
    management: hook_application.HookManagementUseCases,
    session_id: str,
)->pydantic.BaseModel:
    action = args.hook_action
    handler = _HOOK_ACTION_HANDLERS.get(action)
    if handler is None:
        raise ValueError(f"Unsupported hook action: {action}")
    return await handler(args, management, session_id)

async  def execute_hooks_command(args: argparse.Namespace)->int:
    """Build an operation-local app project and run a top-level Hook Command"""
    requested_session =(
        getattr(args, "action_session", None) or getattr(args, "hook_session", None) or getattr(args, "session", None))
    if not requested_session:
        print("ERROR: rabbit hooks requires --session <session-id>")
        return 2

    runtime = None
    app: RabbitApplication | None = None
    try:
        #1.解析session ID（支持前缀匹配）
        session_id = _resolve_session_id(requested_session)
        project_name = SessionPersistence.get_project_name(session_id)
        if not project_name:
            raise LookupError(f"Session ID {session_id} has no persisted project directory")
        bootstrap_context()
        runtime= await build_runtime(
            project_name=project_name,
            session_id=session_id,
            is_resume=True,
            current_model_id= get_default_mode()
        )
        # 3. 获取hook管理用例
        app =RabbitApplication(runtime.app_config)
        management = app.hook_management
        if management is None:
            raise RuntimeError(f"Hook management is unavailable for session ID {session_id}")
        # 4. 分发到具体handler
        result = await execute_hook_action(args,management,session_id)
        output_format = getattr(args, "output_format", None) or getattr(args, "format", "text")
        # 5 渲染结果 (text 或json)
        print(render_hook_result(result,output_format=output_format))
        return 0
    except (LookupError, OSError, ValueError, pydantic.ValidationError, RuntimeError) as exc:
        print(f"Error:{exc}")
        return 1
    finally:
        if app is not None:
            await app.dispose_hook_resources()
        if runtime is not None:
            pathlib.Path(runtime.temp_config_path).unlink(missing_ok=True)

def config_workspace_version(subparsers) -> None:
     workspace_parser =subparsers.add_parser("workspace_version", help="Manage workspace checkpoints and rollback plan")
     workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_version_action", required=True)

     for action in ("status", "list","create","dry-run", "rollback"):
         parser = workspace_subparsers.add_parser(action)
         parser.add_argument("--project-home",default=os.getcwd(), help="Workspace root path")
         parser.add_argument("--session-id",default=None, help="App/session ID")
         parser.add_argument("--format",choices=["text","json"], default="text")
         if action == "create":
             parser.add_argument("--path",action="append", default=[], help="Path to include in checkpoint")
         if action in {"dry-run","rollback"}:
             parser.add_argument("checkpoint_id")
         if action == "rollback":
             parser.add_argument("--yes",action="store_true", help="Execute Rollback after dry-run review")


def _build_service(args: Namespace) -> WorkspaceVersionService:
    workspace_root = Path(args.project_name).resolve()
    if args.session_id:
        return WorkspaceVersionService(
            workspace_root=workspace_root,
            store=FileCheckpointStore(get_app_storage_dir() / f"{args.session_id}" / "workspace_versions"),
            app_id=args.session_id,
            actor="cli",
        )
    return build_workspace_version_service(
        workspace_root=workspace_root,
        actor="cli",
    )

def _emit(args:Namespace, model)-> int:
    payload = model.model_dump(mode='json')
    if args.format =="json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(_format_payload(payload))
    return 0


def _emit_payload(args: Namespace, model, payload:dict, text:str)-> int:
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(text)
    return 0

def _format_payload(payload: dict) -> str:
    if "checkpoint_id" in payload and "files" in payload:
        return _format_rollback_plan(payload)
    if "checkpoint_id" in payload:
        path = ",".join(item["relative_path"] for item in payload.get("files",[]))
        return (
            f"checkpoint_id:{payload['checkpoint_id']}\n"
            f"state: {payload['state']}\n"
            f"reason: {payload['reason']}\n"
            f"path: {path}\n"
        )
    if "checkpoint_count" in payload:
        dirty= payload.get("dirty_summary",{})
        return (
            f"workspace_root:{payload.get('workspace_root')}\n"
            f"branch:{payload.get('branch')}\n"
            f"checkpoint_count:{payload.get('checkpoint_count')}\n"
            f"latest_checkpoint_id:{payload.get('latest_checkpoint_id')}\n"
            f"modified:{len(dirty.get('modified_paths',[]))},"
            f"staged:{len(dirty.get('staged_paths',[]))},"
            f"untracked:{len(dirty.get("untracked_paths",[]))}"
                )
    if "status" in payload:
        return (
            f"status:{payload.get('status')}\n"
            f"checkpoint_id:{payload.get('checkpoint_id')}\n"
            f"applied_paths:{','.json(payload.get('applied_paths',[]))}\n"
            f"blocked_paths:{','.join(payload.get('blocked_paths',[]))}\n"
            f"failed_paths:{','.join(payload.get('failed_paths',[]))}\n"
            f"recovery_bundle:{payload.get('recovery_bundle')}\n"
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)

def _format_rollback_plan(payload: dict) -> str:
    lines=[
        f"checkpoint_id:{payload['checkpoint_id']}",
        f"has_conflicts:{payload['has_conflicts']}",
    ]
    for item in payload.get("files"):
        suffix=""
        if item.get("conflict_type"):
            suffix_=f"({item['conflict_type']})"
        lines.append(f"-{item['relative_path']}:{item.get('action')}{suffix}")
    preserved = payload.get("preserved_paths",[])
    if preserved:
        lines.append(f"preserved_paths:{','.join(preserved)}")
    return "\n".join(lines)


def  run_workspace_version_command(args: Namespace)->int:
     """Run the workspace version CLI commands"""
     service= _build_service(args)
     action = args.workspace_version_action
     if action == "status":
         return _emit(args,service.get_status())
     if action == "list":
         checkpoints = [item.model_dump(mode='json') for item in service.list_checkpoints()]
         return _emit_payload(args,{"checkpoints":checkpoints}, _format_checkpoint_list(checkpoints))
     if action == "create":
         checkpoint =service.create_checkpoint(
             paths=args.path or None,
             reason=CheckpointReason.MANUAL,
             actor="cli",
         )
     if action == "dry-run":
         return _emit(args,service.plan_rollback(args.checkpoint_id))
     if action == "rollback":
         if not args.yes:
             plan = service.plan_rollback(args.checkpoint_id)
             _emit(args,plan)
             return 0
         return _emit(args,service.rollback(args.checkpoint_id,confirm=True))
     raise ValueError(f"Unsupported workspace-version action: {action}")

def _format_checkpoint_list(checkpoints:list[dict])->str:
    if not checkpoints:
        return "No checkpints"
    lines=[]
    for checkpoint in checkpoints:
        paths =",".join(item["relative_path"] for item in checkpoint.get("files",[]))
        lines.append(
            f"{checkpoint['checkpoint_id']} {checkpoint.get('state')} "
            f"{checkpoint.get('reason')} {checkpoint.get('create_at')}  {paths}"
        )
    return "\n".join(lines)









