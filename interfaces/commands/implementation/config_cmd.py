
import json
import logging
from typing import List
from domains.agent.domain.value_objects.runtime_context import get_user_config_file
import domains.llm.provider.domain.primary_profile_selection as provider_domain
from interfaces.commands.protocol import CommandProtocol, CommandResult

logger = logging.getLogger(__name__)


class ModelCommand(CommandProtocol):
    name = "model"
    description = "View or switch the LLM model"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/model 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “model: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )


class ThemeCommand(CommandProtocol):
    name = "theme"
    description = "View or switch the UI theme"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/theme 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “theme: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )

class ConfigCommand(CommandProtocol):
    """
    配置命令
    """

    name = "config"
    description = "Configure RabbitAgent settings(LLM provider, model, etc.)"

    def __init__(self):
        self._prompt_session = None
        self._config_path = get_user_config_file()
        self._config_manager = None

    async def execute(self, args: List[str], context=None) -> CommandResult:
        """
        执行配置命令
        """

        try:
            # load existing config if present
            existing_config = self._load_config()
            if args:
                config = await self._gather_config_from_args(args, existing_config)
            else:
                config = await self._gather_config(existing_config)

            # Save configuration
            self._save_config(config)
            return CommandResult(success=True,message=f"Configuration saved to {self._config_path}")

        except Exception as e:
            logging.error(f"配置命令执行失败: {e}")
            return CommandResult(success=False, message=str(e))

    async  def _gather_config_from_args(self, args: List[str], existing_config:dict)->dict:
        normalized = [arg.strip().lower() for arg in args if arg.strip()]
        if normalized in (["provider","codeagent"],["codeagent"]):
            return await self._configure_codeagent(existing_config)
        raise ValueError("Usage:/config[provider codeagent]")

    async def _gather_config(self, config:dict)->dict:
        pass
        #todo

    async def _configure_codeagent(self, config: dict) -> dict:
        pass
        #todo

    def _encrypt_sensitive_fields(self, config_data:dict)->None:
        pass
        #todo


    def _save_config(self, config:dict)->None:
        """Save configure to file
        """
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        existing_config = self._load_config()
        cli_fields = ["llmProvider","llmModelName","llmApiKey","llmApiUrl","max_tokens"]
        for field in cli_fields:
            if field in config:
                existing_config[field] = config[field]

        self._encrypt_sensitive_fileds(existing_config)

        with open(self._config_path, "w") as f:
            json.dump(existing_config, f, indent=2)

        import os
        os.chmod(self._config_path, 0o600)


    def  _load_config(self)->dict:
        """
        Load existing configuration.

        """
        if self._config_path.exists():
            try:
                with open(self._config_path, "r") as f:
                    config = json.load(f)
                    active_profile =self._get_active_profile(config)
                    if active_profile:
                        config["llmProvider"] = active_profile.get("provider",config.get("llmprovider","openai"))
                        config["llmModelName"] = active_profile.get("modelName", config.get("llmModelName",""))
                        config["llmApiKey"] = active_profile.get("apiKey", config.get("llmApiKey",""))
                        config["llmApiUrl"] = active_profile.get("baseUrl", config.get("llmApiUrl",""))
                    return config
            except (json.JSONDecodeERROR, IOError):
                    return {}
        return {}


    def _get_active_profile(self,config:dict)->dict:
        llm_configs = config.get("llm_configs",[])
        if not isinstance("llm_configs", list):
            return {}
        return provider_domain.select_preferred_active_mapping(llm_configs)