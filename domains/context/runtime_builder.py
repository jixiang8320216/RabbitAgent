import asyncio
import json
import  threading
from  pathlib import Path
from typing import Sequence
import logging
import tempfile
import sys
from copy import deepcopy

from domains.configuration.domain.model.llm_profiles import RuntimeLLMProfile
from domains.mcp.domain.server_configuration import MCPServerConfig
from pydantic import ConfigDict, BaseModel, InstanceOf, TypeAdapter,JsonValue
# ``ModelInfo`` / ``SessionMode`` 的定义本体在配置域的 ``runtime_descriptors``；
# ``interfaces.acp.schema`` 只是 re-export。直接引用本体，避免领域层反向依赖
# 展示层（架构规则 R3）。拿到的仍是同一个类，行为不变。
from domains.configuration.domain.model.runtime_descriptors import (
    ModelInfo,
    SessionMode,
)

from domains.configuration.domain.model.llm_profiles import RuntimeModelProfileOverride
from domains.memory.application.memory_runtime_service import MemoryRuntimeSettings
from domains.configuration.domain.model.app_config import AppConfig
from domains.agent.domain.value_objects.runtime_context import get_runtime_context_or_none
from domains.configuration import ConfigData, ConfigMapping, load_merged_config, parse_config_data
from domains.configuration.infrastructure.adapter import app_mode_resolver as app_mode_loader
from domains.configuration import get_user_data_dir,get_apps_dir,get_logs_dir,get_log_file,get_config_dir,get_bundled_mcp_node_bin_dir
from domains.agent import RuntimeContext,set_runtime_context
import domains.llm.provider.composition as  provider_composition
from domains.skill.infrastructure.gateway.hermes_gateway import HermesGatewayService
from domains.skill.infrastructure.lifecycle import build_and_install_lifecycle_notifier
from domains.configuration.infrastructure.merge.memory_config_rules import resolve_memory_enabled
from domains.configuration.infrastructure.store.user_config_store import read_user_config
from domains.configuration.infrastructure.merge.config_merger import resolve_llm_runtime,load_local_mcp_servers
from domains.configuration.domain.runtime_state.snapshot import RuntimeConfigSnapshot
from domains.configuration.domain.model.llm_config import  clean_llm_config_for_save
from domains.configuration.domain.model.llm_config import LLMConfig
from domains.configuration.domain.options.app_options import RuntimeAppConfigOptions
from domains.expert import  LayeredExpertConfigurationSource
from domains.agent.domain.aggregates.runtime_bindings import get_lifecycle_notifier
from domains.configuration.domain.runtime_state.system_resources import SystemResources,MCPServerResource
from domains.configuration.infrastructure.merge.config_merger import DEFAULT_MODES
from domains.llm.provider.application.context.context_window_resolver import get_context_window_for_model
from domains.resource_catalog.infrastructure import (
      LocalHookConfigurationStore,
      LocalHookFileWatcher,
      LocalSkillDiscoveryService,
      LocalHookResourceLoader,
      LocalInstructionResourceLoader
)


_PROCESS_GATEWAY: HermesGatewayService |None= None
JSON_CONFIG_ADAPTER=TypeAdapter(dict[str,JsonValue])
#并发下保护锁
_process_gateway_lock = threading.Lock()

logger = logging.getLogger(__name__)

def _install_process_gateway()->HermesGatewayService:

    global _PROCESS_GATEWAY
    with _process_gateway_lock:
            if _PROCESS_GATEWAY is None:  # Initialize the gateway once.
                _PROCESS_GATEWAY = HermesGatewayService()
    return _PROCESS_GATEWAY  # Return the shared gateway instance.
           

def build_runtime_context(
    *,
    port: int | None = None,
    config_overrides: ConfigData | None = None,
)-> RuntimeContext:
    # 1. 配置覆盖
    merged_config = dict(load_merged_config())
    if config_overrides:
        merged_config.update(config_overrides)

    #2. 构造RuntimeContext : core层通过get_runtime_context()获取这些路径和配置，不做本地文件发现（这是agents.md中核心红线之一）。
    ctx = RuntimeContext(
         user_data_home= get_user_data_dir(),  # 用户数据根目录 $RABBIT_USER_HOME 或~/.rabbit/
         merged_config=merged_config,
         apps_dir=get_apps_dir(), #APP v3持久化的根目录，每个会话一个app_{id}/文件夹 $RABBIT_APPS_DIR 或~/.rabbit/apps/
         logs_dir=get_logs_dir(), #日志目录  ~/.rabbit/logs/
         log_file=get_log_file(port), #多实例日志查分 ~/.rabbit/logs/rabbit-{port}.log或 rabbit.log
         system_config_dir= get_config_dir(), # rabbit/config/ 随包分发的系统资源（skills/plugins/agents/MCP configs/logging config）
         bundled_mcp_node_bin_dir = get_bundled_mcp_node_bin_dir(), #内置MCP Node 可执行文件目录，找不到时为None, {python_dir}/tools/mcp_node_packages/node_modules/.bin
         default_project_home= Path.cwd(), #进程级默认项目根目录，当前工作目录
         )
        
    #4. 安装到ContextVar
    set_runtime_context(ctx)

    # 5. 初始化Provider运行时
    provider_composition.initialize_provider_runtime()

    # 6. 安装生命周期通知器 在harness 启动阶段异步完成[构建dispatcher、构造notifier + 安装到rabbit-core进程槽]，让runtime调用点（orchestrator、reAct agent、/api/self-evolutuion/user_crrection路由））
    # 能通过统一槽位拿到notifier，向skill Evolution的心跳通道投递生命周期信号
    build_and_install_lifecycle_notifier(gateway=_install_process_gateway())

    return ctx


def bootstrap_context(
    *,
    port: int | None = None,
    config_overrides: ConfigData | None = None,
) -> RuntimeContext:
    """安装进程级运行时上下文。

    这是 :func:`build_runtime_context` 的**向后兼容别名** ——
    旧代码用 ``bootstrap_context()`` 表达「把运行时上下文装上」这件事，
    新代码用更明确的 ``build_runtime_context()``。

    两者的实际行为完全相同：构造 :class:`RuntimeContext`、安装到 ContextVar、
    初始化 provider 运行时、装配生命周期通知器。

    **历史缺口**：本函数此前在迁移中丢失，而
    ``interfaces/cli/args_parse.py`` 与 ``main.py`` 仍在调用它，
    导致 CLI 启动路径直接 ImportError。这里按原语义补回。
    """
    return build_runtime_context(port=port, config_overrides=config_overrides)


class _RuntimeBuildModel(BaseModel):
    model_config= ConfigDict(arbitrary_types_allowed=True,extra="forbid")

class ACPRuntimeDescriptor(_RuntimeBuildModel):
    merged_config: ConfigData
    llm_config: LLMConfig
    available_models: list[ModelInfo]
    current_model_id: str
    available_modes: list[SessionMode]
    current_mode_id: str

class ACPRuntimeBuild(ACPRuntimeDescriptor):
    app_config: InstanceOf[AppConfig]
    temp_config_path: str

def _resolve_global_memory_enabled()->bool:
    return resolve_memory_enabled(read_user_config())

def load_runtime_merged_config()->ConfigData:
    return load_merged_config()


async def build_runtime_descriptor(
    *,
    project_home:str,
    current_mode_id:str,
    selected_model_id:str |None= None,
    extra_mcp_servers:Sequence[ConfigData] | None = None,
    model_profile_override:RuntimeModelProfileOverride|None = None,
)->ACPRuntimeDescriptor:
    merged_config =load_runtime_merged_config()
    override = RuntimeModelProfileOverride.from_raw(model_profile_override)
    # 解析lLM运行时   # 从合并后的merged_config 解析出当前运行时实际使用的LLM配置，返回三元组（LLMConfig，available_models, current_model_id）. 是LLM配置选择的最终裁决点，统一处理【
    # # ACP覆盖>显式选择>激活profile自动选> legacy兜底】四级优选级。
    llm_config, avalable_models, current_model_id = resolve_llm_runtime(
        merged_config,
        selected_model_id,
        model_profile_override =override,
    )
    merged_config = dict(merged_config)
    mcp_servers = await load_local_mcp_servers(project_home)
    if extra_mcp_servers:
        mcp_servers.extend(extra_mcp_servers)
    merged_config.update(parse_config_data({"mcp_servers":mcp_servers}))

    return ACPRuntimeDescriptor(
        merged_config=merged_config,
        llm_config=llm_config,
        available_models=avalable_models,
        current_model_id=current_model_id,
        available_modes= list(DEFAULT_MODES),
        current_mode_id= current_mode_id,
    )

def _prepare_runtime_config_data(
    *,
    merged_config:ConfigMapping,
    session_id:str|None,
    is_resume:bool,
    disabled_plugins:Sequence[str]|None,
    model_profile_override: RuntimeModelProfileOverride |ConfigMapping|None,
)-> tuple[ConfigData,list[str]]:
    override = RuntimeModelProfileOverride.from_raw(model_profile_override)
    snapshot=(
        RuntimeConfigSnapshot.from_merged_config(merged_config)
        .with_session_config(session_id=session_id,is_resume=is_resume)
        .with_model_profile_override(override)
    )

    final_disabled_plugins = snapshot.plugins.resolve_disabled(disabled_plugins)
    snapshot = snapshot.with_disabled_plugins(final_disabled_plugins)
    return clean_llm_config_for_save(snapshot.to_config_data()), final_disabled_plugins

def _write_runtime_config_file(config_data: ConfigData)->str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="rabbit_harness_",
        delete=False,
        encoding="utf-8",
    ) as handle:
     json.dump(config_data,handle,ensure_ascii=False, indent=2)
     return handle.name

def _mcp_server_config_from_data(data: ConfigMapping)->MCPServerConfig:
    config=dict(data)
    if not config.get("description"):
        config["description"] =  config.get("name") or "harness-injected MCP"
    return MCPServerConfig.from_dict(TypeAdapter(dict[str,JsonValue]).validate_python(config))

def _get_core_config_dir()->Path:
    """定位内置配置目录（``config/``，含config.py / technical_config.py）。

    修改前
    ------
    原实现是::

        rabbit_core_file = ""
        # rabbit_core_file = rabbit_core.__file__      # ← 被注释掉了
        return Path(rabbit_core_file).parent / "config"

    ``Path("")``等价于 ``Path(".")``，于是返回的是**当前工作目录**下的
    ``config``。源项目从自身根目录启动，CWD 恰好是那条 ``config/`` 所在目录，
    于是「错得刚刚好」能用；而且 ``rabbit_core_file = ""`` 永远不是 ``None``，
    紧跟着的 ``ifrabbit_core_file is None: raise`` 是一段死代码。

    迁移到 DDD 分层后包路径变了（``config`` 已落位到 ``infrastructure/config``），
    CWD 是项目根而项目根下并没有 ``config/`` 目录，
    ``root_agent_prompts_dir`` 于是指向一个不存在的路径，
    ``RootAgentLoader`` 加载不到任何提示词 → root agent 配置为 ``None``
    → ``run()`` 在 ``if not plan_agent_config`` 处直接 return，
    表现为「TUI 起得来、能敲命令，但一问就再无响应」。

    现在改为**从真实模块位置**推导，不再依赖 CWD。
    """
    try:
        # 配置模块已迁至 domains/configuration/config.py
        # （原 infrastructure/config/ 在 DDD 重构中拆分为
        #   shared/technical_config.py —— 数据配置
        #   domains/configuration/config.py —— 技术配置）
        import domains.configuration.infrastructure.logging_bootstrap as core_config_pkg

        pkg_file = getattr(core_config_pkg, "__file__", None)
        if pkg_file:
            return Path(pkg_file).resolve().parent
    except Exception:
        logger.debug("fallback to CWD-based core config dir", exc_info=True)

    # 兜底：包导入失败时退回 CWD（保持与源项目一致的旧行为）
    return Path("config")

def _detect_bundled_runtime_prompt()->str|None:

    try:
        _python_path= Path(sys.executable).resolve()
        _candidate_roots=[_python_path.parent]
        if len(_python_path.parents)>2:
            _candidate_roots.append(_python_path.parent.parents[2])

        for _instdir in _candidate_roots:
            _node_exe = _instdir/"tools"/"node"/"node.exe"
            if not _node_exe.exists():
                continue
            _git_exe = _instdir/"tools"/"git"/"cmd"/"git.exe"
            _git_bash= _instdir/"tools"/"git"/"usr"/"bin"/"bash.exe"
            _has_bundled_git = _git_exe.exists()
            _git_lines=""
            if _has_bundled_git:
                _git_lines = f"- **Git**: '{_git_exe.as_posix()}'\n- **Git Bash**: '{_git_bash.as_posix()}'\n"
            _shell_note=(
                "\n 当前环境已包含Git Bash， shell 命令会自动使用git-bash 执行，"
                "支持标准U逆向命令语法（ls,grep,cat,head,sed,awk 等）。"
                if _has_bundled_git
                else ""
            )
            return (
                "## 运行环境说明\n\n"
                "当前运行在 OfficeClaw离线安装环境中，**没有系统级Python/Node.js**。"
                "执行任何脚本或命令时，必须使用以下bundled可执行文件，否则命令找不到解释器：\n\n"
                f"- **Python**: '{(_instdir/'tools'/'python'/'python.exe').as_posix()}'\n'"
                f"- **Node.js**: '{(_instdir/'tools'/'node'/'node.exe').as_posix()}'\n'"
                f"{_git_lines}"
                f"- **安装目录(INSTDIR)**: '{_instdir.as_posix()}'\n'"
                "在生成shell命令、边界脚本、调用工具时，始终使用上述完整路径，"
                "不要依赖 ‘python‘、‘node‘、’npm’等系统PATH命令。"
                f"{_shell_note}"
            )
    except (OSError,ValueError) as exc:
        #路径解析失败（如sys.executable不可用、路径越界）是可恢复的降级场景
        #bundled runtime prompt 仅用于提示信息，缺失不影响核心功能
        #但操作员应能感知到检测失败，因此升级为warning并包含完整异常信息。
        logger.warning(exc)
        return None

def _build_system_resources(
    *,
    project_home:str,
    runtime_user_data_home,
    project_config_dir:Path,
    merged_config:ConfigMapping,
)->SystemResources:
    """组装SystemResources对象，包含hook/instruction/skill/MCP"""
    runtime_options= RuntimeAppConfigOptions.from_config(merged_config)
    mcp_server_configs = [_mcp_server_config_from_data(item) for item in runtime_options.mcp_server_config_data()]
    mcp_server_resources: list[MCPServerResource]=[config for config in mcp_server_configs]
    hook_resource_loader=LocalHookResourceLoader(
        project_home=project_home,
        user_data_home=runtime_user_data_home,
        system_config_dir = project_config_dir,
    )
    hook_resources = hook_resource_loader.load()
    instruction_resources= LocalInstructionResourceLoader(
        project_home=project_home,
        user_data_home=runtime_user_data_home,
    ).load_session_start()
    skill_discovery_service = LocalSkillDiscoveryService(
        project_home=project_home,
        user_data_home=runtime_user_data_home,
        system_config_dir=project_config_dir,
    )
    skill_registry= skill_discovery_service.discover_all()
    return SystemResources(
        skill_registry=skill_registry,
        skill_registry_refresher = skill_discovery_service,
        rabbit_skills_md = skill_discovery_service.load_rabbit_skills_md(),
        instruction_resources=instruction_resources,
        mcp_servers = mcp_server_resources,
        hooks_config = hook_resources.snapshot,
        hook_policy= hook_resources.policy,
        hook_configuration_paths = hook_resources.configuration_paths,
        hook_configuration_fingerprint = hook_resources.fingerprint,
        hook_diagnostics =hook_resources.diagnostics,
        hook_configuration_refresher = hook_resource_loader,
        hook_component_context_updater = hook_resource_loader,
        hook_file_watcher = LocalHookFileWatcher(),
        hook_configuration_store = LocalHookConfigurationStore(hook_resource_loader),

    )

def _build_runtime_context(
    *,
    project_home:str,
    runtime_user_data_home,
    project_config_dir:Path,
    merged_config:ConfigMapping,
)->None:

    """构建并设置RuntimeContext ，安装lifecycle notifier
    """

    existing_context = get_runtime_context_or_none()
    if existing_context is not None and existing_context.default_project_home is not None:
        default_project_home = existing_context.default_project_home
    elif project_home:
        default_project_home = Path(project_home)
    else:
        default_project_home = Path.cwd()
    runtime_ctx = RuntimeContext(
        user_data_home=runtime_user_data_home,
        merged_config=merged_config,
        apps_dir= get_apps_dir(),
        logs_dir= get_logs_dir(),
        log_file=get_log_file(),
        system_config_dir=project_config_dir,
        bundled_mcp_node_bin_dir=get_bundled_mcp_node_bin_dir(),
        default_project_home=default_project_home,
    )
    set_runtime_context(runtime_ctx)

    provider_composition.initialize_provider_runtime()

    if get_lifecycle_notifier() is None:
        build_and_install_lifecycle_notifier(gateway=_install_process_gateway())



async def build_runtime(
    *,
    project_home: str,
    session_id: str | None,
    is_resume: bool,
    current_mode_id: str,
    selected_model_id: str | None = None,
    extra_mcp_servers: Sequence[ConfigData] | None = None,
    disabled_plugins: Sequence[str] | None = None,
    model_profile_override: RuntimeModelProfileOverride | ConfigMapping | None = None,
) -> ACPRuntimeBuild:
    # 阶段1 构建RuntimeDescriptor  配置合并+llm解析+MCP装配三件事，产出不含AppConfig的轻量描述符ACPRuntimeDescriptor，它是build_runtime的字步骤,专注于运行期可选项的解析与合并，不涉及AppConfig构造、
    #临时文件落盘、RuntimeContext设置等副作用。
    descriptor = await  build_runtime_descriptor(
        project_home=project_home,
        current_mode_id=current_mode_id,
        selected_model_id = selected_model_id,
        extra_mcp_servers = extra_mcp_servers,
        model_profile_override = model_profile_override,
    )

    # 阶段2 准备运行时配置数据并落盘
    config_data,final_disabled_plugins = _prepare_runtime_config_data(
        merged_config=descriptor.merged_config,
        session_id=session_id,
        is_resume = is_resume,
        disabled_plugins= disabled_plugins,
        model_profile_override = model_profile_override,
    )

    temp_config_path = _write_runtime_config_file(config_data)

    # 阶段4 装配周边资源
    project_config_dir = get_config_dir()
    runtime_user_data_home = get_user_data_dir()
    resolverd_project_home = Path(project_home).resolve(strict=False) if project_home else None
    from domains.configuration.infrastructure.migration.memory_document_migration import initialize_memory_documents

    memory_initialization = initialize_memory_documents(
        user_data_home=runtime_user_data_home,
        project_home=resolverd_project_home,
    )

    merged_config=descriptor.merged_config
    runtime_options = RuntimeAppConfigOptions.from_config(merged_config)
    mcp_server_configs=[_mcp_server_config_from_data(item) for  item in runtime_options.mcp_server_config_data()]

    #  阶段5 异步构建system resources
    system_resources = await asyncio.to_thread(
        _build_system_resources,
        project_home = project_home,
        runtime_user_data_home = runtime_user_data_home,
        project_config_dir = project_config_dir,
        merged_config = merged_config,
    )


    # 阶段6 构建RuntimeContext
    _build_runtime_context(
        project_home = project_home,
        runtime_user_data_home = runtime_user_data_home,
        project_config_dir = project_config_dir,
        merged_config = merged_config,
    )
    # 阶段7 探测bundled runtime prompt
    _build_runtime_prompt = _detect_bundled_runtime_prompt()

    # 阶段8 构建AppConfig（核心装配）
    try:
        expert_configuration_source = LayeredExpertConfigurationSource(
           user_data_home=runtime_user_data_home,
           builtin_root= _get_core_config_dir()/"experts",
              system_root = project_config_dir/"experts",
        )
        app_config = AppConfig(
            llm_config=descriptor.llm_config,
            config_file = temp_config_path,
            sub_agents_dir = str(project_config_dir/".agents"),
            root_agent_prompts_dir = str(_get_core_config_dir()),
            user_project_home =project_home,
            project_system_prompt = _build_runtime_prompt,
            app_mode =current_mode_id or app_mode_loader.load_app_mode(merged_config),
            prune_enabled= runtime_options.prune_enabled,
            prune_turns_to_protect= runtime_options.prune_turns_to_protect,
            prune_tokens_to_project = runtime_options.prune_tokens_to_project,
            prune_minimum_tokens = runtime_options.prune_minimum_tokens,
            prune_trigger_threshold = runtime_options.prune_trigger_threshold,
            prune_projected_tools = runtime_options.prune_protected_tools,
            disabled_plugins = final_disabled_plugins,
            mcp_servers= mcp_server_configs,
            system_resources = system_resources,
            expert_configuration_source = expert_configuration_source,
            runtime_config_snapshot = JSON_CONFIG_ADAPTER.validate_python(deepcopy(dict(merged_config))),
            memory_settings = MemoryRuntimeSettings(
                enabled = _resolve_global_memory_enabled(),
                user_memory_file = memory_initialization.user_memory_file,
                project_memory_file = memory_initialization.project_memory_file,
            ),
            microcompact_pct= runtime_options.microcompact_pct,
            auto_compact_pct= runtime_options.auto_compact_pct,
            keep_recent_tools = runtime_options.keep_recent_tools,
            context_window_size = _resolve_context_window_size(
                merged_config = merged_config,
                current_model_id = descriptor.current_model_id,
                model_name= descriptor.llm_config.model_name,
                legacy_context_window_size = None,
             ),
        )

        #阶段10 返回
        return ACPRuntimeBuild(
            merged_config = descriptor.merged_config,
            llm_config = descriptor.llm_config,
            available_models = descriptor.available_models,
            current_model_id = descriptor.current_model_id,
            available_modes = descriptor.available_modes,
            current_mode_id = descriptor.current_mode_id,
            app_config = app_config,
            temp_config_path = temp_config_path,
        )
    except Exception:
        Path(temp_config_path).unlink(missing_ok=True)   # 阶段9 异常清理
        raise



def _resolve_context_window_size(
    *,
    merged_config: ConfigMapping,
    current_model_id: str,
    model_name: str,
    legacy_context_window_size: int | None = None,
) -> int | None:
    profiles = RuntimeLLMProfile.list_from_raw(merged_config.get("llm_configs"))
    selected_profile = next(
        (profile for profile in profiles if profile.profile_id== current_model_id ),
        None,
    )

    profile_context_window_size = selected_profile.context_window_size if selected_profile else None
    if profile_context_window_size is not None and profile_context_window_size >0:
        return profile_context_window_size
    if selected_profile  and selected_profile.metadata:
        metadata_cw= selected_profile.metadata.get("context_window")
        if isinstance(metadata_cw, int) and metadata_cw >0:
            return metadata_cw
    if legacy_context_window_size is not None and legacy_context_window_size > 0:
        return legacy_context_window_size
    return get_context_window_for_model(model_name)

