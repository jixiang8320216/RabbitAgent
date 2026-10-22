import json
import os
import logging
from pathlib import Path
from collections.abc import Mapping

from domains.configuration.domain.model.llm_profiles import RuntimeModelProfileOverride, RuntimeLLMProfiled, RuntimeLLMProfile
from domains.llm.provider.domain import select_preferred_active_profile
from shared.paths import get_config_dir, get_user_config_dir
from domains.configuration.domain import ConfigData, ConfigMapping, parse_config_data
import domains.configuration.infrastructure.adapter.app_mode_resolver as app_mode_loader
from domains.runtime.application.runtime_model_config import normalize_profiles_with_stable_ids
from domains.configuration.domain.model.llm_config import LLMConfig
from domains.llm.provider.application.configuration.resolution import resolve_profile_proxy_configuration
from domains.configuration.domain.model.runtime_descriptors import ModelInfo, SessionMode
from domains.permission.infrastructure.security.credential_encryption import  EncryptionService,get_encryption_service
from domains.configuration.domain.model.mcp_server_config import  RuntimeMCPTransport,RuntimeMCPServerConfig
from domains.resource_catalog.infrastructure.local_mcp_resources import  LocalMCPDiscoveryService
import domains.llm.provider.domain.openai_api_protocol as openai_api_protocol
from domains.configuration.domain.model.llm_config import normalize_llm_config_for_read
logger = logging.getLogger(__name__)

DEFAULT_MODES=[]
DEFAULT_MODES: tuple[SessionMode, ...] = (
    SessionMode(
        id="delegate",
        name="Delegate",
        description="Delegate work to specialized sub-agents.",
    ),
    SessionMode(
        id="roleplay",
        name="Roleplay",
        description="Interact through a configured agent role.",
    ),
    SessionMode(
        id="solo",
        name="Solo",
        description="Complete the task with a single agent.",
    ),
    SessionMode(
        id="guarded",
        name="Guarded",
        description="Apply additional safety constraints during execution.",
    ),
    SessionMode(
        id="groupchat",
        name="Group Chat",
        description="Coordinate multiple agents in a shared conversation.",
    ),
    SessionMode(
        id="pipeline",
        name="Pipeline",
        description="Run multiple processing stages in a fixed order.",
    ),
    SessionMode(
        id="parallel",
        name="Parallel",
        description="Run independent agent tasks concurrently and combine results.",
    ),
    SessionMode(
        id="debate",
        name="Debate",
        description="Use opposing agents and a judge to evaluate proposals.",
    ),
)

def _plugin_config_layer(plugins: ConfigMapping)->ConfigData|None:
    #1. 提取“config” 键
    raw_config = plugins.get("config")
    if raw_config is None:
        plugin_config: ConfigData = {}  #无config-》空dict
    elif isinstance(raw_config, Mapping):
        plugin_config = parse_config_data(raw_config) #正常dict-》校验
    else:
        return None             #非dict -> 返回None(上层跳过深度合并)
    #2.旧格式迁移： disabled 列表-> config.task_artifact.enabled = false
    # task_artifact_config = plugin_config.get("task_artifact")
    # has_explicit_switch= isinstance(task_artifact_config, Mapping) and "enabled" in task_artifact_config
    # disabled = plugins.get("disabled")
    # can_promote_legacy_switch =task_artifact_config is None or isinstance(task_artifact_config, Mapping)
    # if(
    #     not has_explicit_switch             #用户没给有显示设置enabled
    #     and can_promote_legacy_switch       #task_artifact 配置是dict 或不存在
    #     and isinstance(disabled, list)      #disabled 是列表
    #     and "task_artifact" in disabled     #task_artifact在disabled 列表中
    # ):
    #     settings = parse_config_data(task_artifact_config) if isinstance(task_artifact_config, Mapping) else {}
    #     settings["enabled"] = False
    #     plugin_config["task_artifact"] = settings
    return plugin_config

def _merge_plugins_configuration_layers(
    system_plugins: ConfigMapping,
    user_plugins: ConfigMapping,
)->ConfigData:
    #浅合并
    merged_plugins: ConfigData = {
        **parse_config_data(system_plugins),
        **parse_config_data(user_plugins),
    }
    system_config=_plugin_config_layer(system_plugins)
    user_config=_plugin_config_layer(user_plugins)
    if system_config is None or user_config is None:
        return merged_plugins

    merged_config = system_config
    for plugin_name, user_plugin_config in user_config.items():
        system_plugin_config = merged_plugins.get(plugin_name)
        if isinstance(system_plugin_config, dict) and isinstance(user_plugin_config, dict):
            merged_config[plugin_name] ={
                **system_plugin_config,
                **user_plugin_config,
            }
        else:
            merged_config[plugin_name] = user_plugin_config
        if merged_config or "config" in system_plugins or "config" in user_config:
            merged_plugins["config"] = merged_config
    return merged_plugins

def load_merged_config(config_path: str|None=None) -> ConfigData:
    """加载安装包级配置和用户级配置，并将两者插件配置融合. """
    package_config_path= get_config_dir()/"config.json"
    merged: ConfigData ={}
    package_config:ConfigData={}

    # 获取package config file content
    if package_config_path.exists():
        try:
          package_config = parse_config_data(json.loads(package_config_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            logger.warning(
                " json decode failed  package config file path %s:%s",package_config_path,exc
            )
            package_config = {}
        merged.update(package_config)

    # 获取 user config file content,Determine user config path with priority: param>env>default
    if config_path:
        user_config_path = Path(config_path)
    elif os.environ.get("RABBIT_CONFIG_PATH"):
        user_config_path = Path(os.environ["RABBIT_CONFIG_PATH"])
    else:
        user_config_path = get_user_config_dir()/"config.json"

    if user_config_path.exists():
        try:
            user_config = parse_config_data(json.loads(user_config_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            logger.warning(
                " json decode failed  user config file path %s:%s",user_config_path,exc
            )
            user_config = {}
        else:
            merged.update(user_config)
            package_plugins = package_config.get("plugins")
            user_plugins = user_config.get("plugins")
            if isinstance(package_plugins, Mapping)and isinstance(user_plugins, Mapping):
                merged["plugins"] = _merge_plugins_configuration_layers(
                    package_plugins, user_plugins
                )
    return parse_config_data(normalize_llm_config_for_read(merged))

def get_default_mode()->str:
    """返回默认应用模式。

    ``load_app_mode`` 自身已保证「配置缺失 -> 落到 DEFAULT_APP_MODE」，
    因此正常路径不会返回 None。这里的异常分支是防御性的：配置读取失败
    （磁盘不可读、JSON 损坏）不应阻断启动，落回同一默认值。
    """
    try:
        return app_mode_loader.load_app_mode((load_merged_config()))
    except(OSError,ValueError) as exc:
        logger.warning("failed to load app_mode from config, using default: %s", exc)
        return app_mode_loader.DEFAULT_APP_MODE
    except Exception:
        logger.error("unexpected failure while resolving default app_mode", exc_info=True)
        raise

def _profile_model(profile:RuntimeLLMProfile |ConfigMapping)->RuntimeLLMProfile:
    parsed = RuntimeLLMProfile.from_raw(profile)
    if parsed is None:
        return RuntimeLLMProfile()
    return parsed

def profile_to_model_info(profile:RuntimeLLMProfile|ConfigMapping)->ModelInfo:
    profile_model = _profile_model(profile)
    profile_name = profile_model.display_name()
    provider = profile_model.provider or "unknown"
    api_url = profile_model.base_url or profile_model.api_url or ""
    description =f"{provider} {api_url}".strip()
    return ModelInfo(model_id=profile_model.profile_id,name=profile_name,description=description or None)

def decrypt_config_value(value:str)->str:
    if not value.startswith(EncryptionService.ENCRYPTION_PREFIX):
        return value
    try:
        return get_encryption_service().decrypt(value)
    except Exception:
        logger.error("decrypt")
        return ""

def _decrypt_optional(value:str|None)->str:
    if value is None:
        return None
    return decrypt_config_value(value)

def profile_to_llm_config(
    profile:RuntimeLLMProfile|ConfigMapping,
    *,
    global_proxy_configuration: ConfigMapping |None=None,
)->LLMConfig:
    profile_model = _profile_model(profile)
    profile_values = profile_model.to_config_data()
    effective_proxy = resolve_profile_proxy_configuration(
        profile_values,
        global_proxy_configuration or {}
    )

    return LLMConfig(
        profile_id= profile_model.profile_id or None,
        provider = profile_model.provider or "openai",
       # service_zone = profile_model.service_zone,
        api_protocol = openai_api_protocol.OpenAIAPIProtocol(profile_model.api_protocol),
        model_name = profile_model.model_name or profile_model.model or "gpt-4",
        base_url = profile_model.api_url or profile_model.base_url or "",
        api_key=_decrypt_optional(profile_model.api_key) or "",
        auth_url= profile_model.auth_url,
        model_api_url = profile_model.model_api_url,
        use_proxy = effective_proxy.use_proxy is True,
        http_proxy = effective_proxy.http_proxy,
        https_proxy = effective_proxy.https_proxy,
        proxy_username = effective_proxy.proxy_username,
        proxy_password = _decrypt_optional(effective_proxy.proxy_password),
        temperature = profile_model.temperature if profile_model.temperature is not None else 0.7,
        reasoning_effort = profile_model.reasoning_effort,
        service_tier = profile_model.service_tier,
        max_tokens= profile_model.max_tokens,
        context_window_size = profile_model.context_window_size,
        metadata = dict(profile_model.metadata) if profile_model.metadata is not None else None,
        thinking_mode = profile_model.thinking_mode,
        custom_headers= dict(profile_model.custom_headers) if profile_model.custom_headers else None

    )

def resolve_llm_runtime(
        merged_config: ConfigMapping,
        selected_model_id:str|None,
        model_profile_override:RuntimeModelProfileOverride|ConfigMapping |None=None,
)->tuple[LLMConfig,list[ModelInfo],str]:
    # Level0 解析profiles 并构造available_module
    raw_profiles = RuntimeLLMProfile.list_from_raw(merged_config.get("llmConfigs") or merged_config.get("llm_configs"))
    profiles = RuntimeLLMProfile.list_from_raw(
        normalize_profiles_with_stable_ids([profile.to_config_data() for profile in raw_profiles])
    )
    available_models = [profile_to_model_info(profile) for profile in profiles]
    # Level1 ACP overide (最高优先级)
    override = RuntimeModelProfileOverride.from_raw(model_profile_override)
    if override is not None and override.has_model():
        override_profile = override.to_llm_profile()
        return(
            profile_to_llm_config(
                override_profile,
                global_proxy_configuration=merged_config,
            ),
            available_models,
            RuntimeLLMProfiled.ACP_OVERRIDE.value,
        )
    selected_profile = None
    # 显式selected_model_id ,自动选择激活的preferred profile
    if selected_model_id:
        selected_profile = next(
            (profile for profile in profiles if profile.profile_id == selected_model_id),
            None,
        )
    if selected_profile is None:
        selected_profile =select_preferred_active_profile(
            profiles,
            is_active=lambda profile: profile.active is True,
            model_type=lambda profile: profile.model_type,
        )
    if selected_profile is not None:
       return (
           profile_to_llm_config(
               selected_profile,
               global_proxy_configuration=merged_config,
           ),
           available_models,
           selected_profile.profile_id
       )
    
    raise RuntimeError(
        "No LLM profile is configured. Add an entry to 'llm_configs' "
        "or configure the legacy LLM provider settings."
    )

# 从project_home 本地发现所有MCP server配置，按transport类型（STDIO/SSE/Streamable HTTP）装配为标准ConfigData字典列表，供build_runtime_descriptor
#合并到runtime配置. 是项目级MCP server注入运行时的唯一入口。
async  def  load_local_mcp_servers(project_home:str)-> list[ConfigData]:
    try:
        registery = LocalMCPDiscoveryService(project_home=project_home).discover_all()
        configs: list[ConfigData] = []
        for mcp in registery.list_all():
           if not mcp.active:
               continue
           transport = RuntimeMCPTransport(mcp.transport)
           server_config = RuntimeMCPServerConfig(
               name=mcp.name,
               description=mcp.description,
               transport=transport,
               scope=mcp.scope.value,
               active=mcp.active,
               timeout=mcp.timeout,
               sse_read_timeout=mcp.sse_read_timeout,
               use_proxy=mcp.use_proxy,
               http_proxy=mcp.http_proxy,
               https_proxy=mcp.https_proxy,
               verify_ssl=mcp.verify_ssl,
           )
           item = server_config.to_config_data()
           item["protocol_version"]= mcp.protocol_version
           item["capabilities"]= mcp.capabilities
           item["skill_schema_validation"]= mcp.skill_schema_validation
           item["allow_missing_shema"] = mcp.allow_missing_shema
           item["strict_schema_validation"] = mcp.strict_schema_validation
           item["validate_parameters"] = mcp.validate_parameters
           if transport in {RuntimeMCPTransport.SSE, RuntimeMCPTransport.STREAMABLE_HTTP}:
               item.update(
                   parse_config_data(
                       {
                           "url": mcp.url,
                           "headers": mcp.headers,
                       }
                   )
               )
           elif transport is RuntimeMCPTransport.STDIO:
               item.update(
                   parse_config_data(
                       {
                           "command": mcp.command,
                           "args": mcp.args,
                           "env": mcp.env,
                           "cmd":mcp.cmd,
                           "encoding": mcp.encoding,
                           "encoding_error_handler": mcp.encoding_error_handler,
                       }
                   )
               )
           configs.append(item)
        return configs
    except Exception as e:
        #mCp 配置加载失败时可恢复的降级场景： 返回空列表让运行时继续启动。 但必须记录完整异常日志（含调用栈），以便操作员排查mcp配置问题
        logger.error(e)
        return []























